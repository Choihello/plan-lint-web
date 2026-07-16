from __future__ import annotations

import concurrent.futures
import threading

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from planlint.llm.client import LLMUnavailable, make_client
from starlette.formparsers import MultiPartParser

from .config import load_settings
from .converters import ConversionError, convert, normalize_pasted
from .lint import run_lint
from .quota import Quota

settings = load_settings()

# 완전 무저장: 멀티파트 스풀 임계값을 미들웨어 허용치 + 여유폭보다 크게 잡아
# SpooledTemporaryFile이 디스크로 넘어가지 않게 한다.
# 참고: 설치된 starlette(1.3.1)에서는 `max_file_size`가 아니라
# `spool_max_size`가 이 임계값을 제어하는 클래스 속성이다 (기본 1MB).
# 미들웨어는 max_file_bytes + 64KiB까지 허용하므로, 스풀은 최소 128KiB 이상 여유가 있어야 한다.
MultiPartParser.spool_max_size = settings.max_file_bytes + 128 * 1024

app = FastAPI(title="plan-lint-web", docs_url=None, redoc_url=None)
quota = Quota(settings.quota_db_path, settings.per_ip_daily, settings.global_daily, settings.quota_salt)
_llm_sem = threading.BoundedSemaphore(settings.llm_concurrency)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(settings.llm_concurrency * 2, 4))

RULES_TIMEOUT = 30  # 룰만 돌 때의 안전 타임아웃(초)
CONVERT_TIMEOUT = 30  # 파일 변환 안전 타임아웃(초)


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        for header in ("fly-client-ip", "x-forwarded-for"):
            value = request.headers.get(header)
            if value:
                return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/api/lint":
        cl = request.headers.get("content-length")
        if cl is None or not cl.isdigit():
            return JSONResponse(status_code=411, content={"error": "요청 크기를 확인할 수 없어요. 일반 브라우저나 표준 HTTP 클라이언트로 시도해주세요."})
        if int(cl) > settings.max_file_bytes + 64 * 1024:
            mb = settings.max_file_bytes // (1024 * 1024)
            return JSONResponse(status_code=413, content={"error": f"파일이 너무 커요. {mb}MB 이하로 올려주세요."})
    return await call_next(request)


@app.get("/api/quota")
def get_quota(request: Request):
    return {"remaining_today": quota.remaining(client_ip(request))}


@app.post("/api/lint")
async def lint(
    request: Request,
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    use_llm: bool = Form(True),
):
    warnings: list[str] = []
    if file is not None and file.filename:
        data = await file.read()  # 메모리에서만 처리 — 디스크 기록 없음 (무저장 원칙)
        if len(data) > settings.max_file_bytes:
            mb = settings.max_file_bytes // (1024 * 1024)
            return JSONResponse(status_code=413, content={"error": f"파일이 너무 커요. {mb}MB 이하로 올려주세요."})
        future = _executor.submit(convert, data, file.filename)
        try:
            result = future.result(timeout=CONVERT_TIMEOUT)
        except concurrent.futures.TimeoutError:
            return JSONResponse(status_code=422, content={"error": "파일 변환이 너무 오래 걸려요. 텍스트 붙여넣기로 시도해주세요."})
        except ConversionError as e:
            return JSONResponse(status_code=422, content={"error": str(e)})
        source, warnings = result.text, result.warnings
    elif text and text.strip():
        result = normalize_pasted(text)
        source, warnings = result.text, result.warnings
    elif text is not None:
        return JSONResponse(status_code=422, content={"error": "붙여넣은 내용이 비어 있어요. 본문을 붙여넣어주세요."})
    else:
        return JSONResponse(status_code=422, content={"error": "파일을 올리거나 텍스트를 붙여넣어주세요."})

    if len(source) > settings.max_text_chars:
        return JSONResponse(
            status_code=413,
            content={"error": f"텍스트가 너무 길어요. {settings.max_text_chars:,}자 이하로 줄여주세요."},
        )
    if not source.strip():
        return JSONResponse(status_code=422, content={"error": "파일에서 텍스트를 찾지 못했어요. 텍스트 붙여넣기로 시도해주세요."})

    ip = client_ip(request)
    llm_client = None
    skipped_reason: str | None = None
    if use_llm:
        skipped_reason = quota.try_consume(ip)
        if skipped_reason is None:
            try:
                llm_client = make_client()
            except LLMUnavailable:
                quota.refund(ip)
                skipped_reason = "llm_error"

    def work():
        if llm_client is not None:
            if not _llm_sem.acquire(timeout=10):
                outcome = run_lint(source)
                outcome.llm_error = True  # 정직 강등 + 환불 경로로 합류
                return outcome
            try:
                return run_lint(source, llm_client)
            finally:
                _llm_sem.release()
        return run_lint(source)

    timeout = settings.llm_timeout_seconds if llm_client is not None else RULES_TIMEOUT
    future = _executor.submit(work)
    try:
        outcome = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        if llm_client is not None:
            quota.refund(ip)
            skipped_reason = "llm_error"
            outcome = run_lint(source)  # 룰만이라도 반환
        else:
            return JSONResponse(status_code=500, content={"error": "검사가 예상보다 오래 걸려요. 잠시 후 다시 시도해주세요."})

    if outcome.llm_error:
        quota.refund(ip)
        skipped_reason = "llm_error"

    return {
        "findings": outcome.findings,
        "converted_text": source,
        "meta": {
            "llm_ran": outcome.llm_ran,
            "llm_skipped_reason": skipped_reason,
            "remaining_today": quota.remaining(ip),
            "conversion_warnings": warnings,
        },
    }


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
