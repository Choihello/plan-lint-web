from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import logging
import threading

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from planlint.llm.client import LLMUnavailable, make_client
from starlette.formparsers import MultiPartParser

from .config import load_settings
from .converters import ConversionError, convert, normalize_pasted
from .lint import run_lint
from .llm_budget import BudgetPool, apply_request_timeout
from .quota import Quota

logger = logging.getLogger("planlint.web")

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

# 변환과 검사를 별도 풀로 분리 — 한쪽이 포화돼도 다른 쪽이 굶지 않는다.
_convert_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=settings.convert_concurrency, thread_name_prefix="convert"
)
_lint_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(settings.llm_concurrency * 2, 4), thread_name_prefix="lint"
)
# 무한정 큐가 쌓이는 대신 빠르게 거절하기 위한 백프레셔 카운터
_inflight = threading.Semaphore(settings.max_inflight_lint)

RULES_TIMEOUT = 30  # 룰만 돌 때의 안전 타임아웃(초)
CONVERT_TIMEOUT = 30  # 파일 변환 안전 타임아웃(초)

SECURITY_HEADERS = {
    # 자체 호스팅 자산만 쓰므로 외부 출처를 전면 차단한다 (인라인 스타일 없음)
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), interest-cohort=()",
    "X-Frame-Options": "DENY",
}


async def _run_bounded(executor, fn, *args, timeout: float):
    """블로킹 작업을 스레드로 보내되 이벤트 루프는 await로 양보한다.

    예전에는 async 핸들러 안에서 Future.result(timeout=)을 직접 호출해, 검사 1건이
    도는 동안 /api/quota를 포함한 모든 요청이 멈췄다(실측 1.5초 차단).
    """
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(executor, fn, *args), timeout=timeout)


def is_admin(request: Request) -> bool:
    """운영자 무제한 통로 — PLW_ADMIN_TOKEN이 설정돼 있고 헤더가 일치할 때만."""
    token = settings.admin_token
    if not token:
        return False
    supplied = request.headers.get("x-admin-token", "")
    return hmac.compare_digest(supplied, token)


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        for header in ("fly-client-ip", "x-forwarded-for"):
            value = request.headers.get(header)
            if value:
                return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    """HTML은 항상 재검증(스타일 개편 시 구버전 잔존 방지), 정적 자산은 ?v= 버스팅 전제로 장기 캐시."""
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    ctype = response.headers.get("content-type", "")
    if request.url.path.startswith("/api/"):
        # 진단 결과에는 문서 본문이 담긴다 — 중간 캐시·디스크에 남지 않게 한다
        response.headers["Cache-Control"] = "no-store"
    elif "text/html" in ctype:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif request.url.path.startswith("/fonts/"):
        response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    elif request.url.path.endswith((".css", ".js")):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response


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
    if is_admin(request):
        return {"remaining_today": quota.remaining_admin(settings.admin_daily), "admin": True}
    return {"remaining_today": quota.remaining(client_ip(request))}


@app.post("/api/lint")
async def lint(
    request: Request,
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    use_llm: bool = Form(True),
):
    if not _inflight.acquire(blocking=False):
        return JSONResponse(
            status_code=503,
            content={"error": "지금 진단 요청이 몰려 있어요. 잠시 후 다시 시도해주세요."},
            headers={"Retry-After": "10"},
        )
    try:
        return await _lint_inner(request, file, text, use_llm)
    finally:
        _inflight.release()


async def _lint_inner(request: Request, file, text, use_llm: bool):
    warnings: list[str] = []
    if file is not None and file.filename:
        data = await file.read()  # 메모리에서만 처리 — 디스크 기록 없음 (무저장 원칙)
        if len(data) > settings.max_file_bytes:
            mb = settings.max_file_bytes // (1024 * 1024)
            return JSONResponse(status_code=413, content={"error": f"파일이 너무 커요. {mb}MB 이하로 올려주세요."})
        try:
            result = await _run_bounded(_convert_executor, convert, data, file.filename,
                                        timeout=CONVERT_TIMEOUT)
        except asyncio.TimeoutError:
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
    admin = is_admin(request)
    llm_client = None
    skipped_reason: str | None = None
    consumed = False  # 쿼터를 실제로 소모했을 때만 환불 (관리자는 소모 자체가 없음)
    if use_llm:
        # 관리자도 일일 상한을 받는다 — 토큰 유출 시 무제한 호출 차단(공개 캡과는 별도 축)
        if admin:
            skipped_reason = quota.try_consume_admin(settings.admin_daily)
            consumed = skipped_reason is None
        else:
            skipped_reason = quota.try_consume(ip)
            consumed = skipped_reason is None
        if skipped_reason is None:
            try:
                # 요청당 호출 예산 + SDK 타임아웃을 씌운다 (관리자도 예외 없음)
                llm_client = BudgetPool(
                    apply_request_timeout(
                        make_client(model=settings.llm_model or None),
                        settings.llm_request_timeout,
                    ),
                    settings.max_llm_calls,
                )
            except LLMUnavailable:
                if consumed:  # 외부 호출 전이므로 비용이 발생하지 않았다
                    quota.refund_admin() if admin else quota.refund(ip)
                skipped_reason = "llm_error"

    def do_refund():
        """소비한 축(관리자/공개)에 맞춰 환불한다."""
        if not consumed:
            return
        if admin:
            quota.refund_admin()
        else:
            quota.refund(ip)

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
    try:
        outcome = await _run_bounded(_lint_executor, work, timeout=timeout)
    except asyncio.TimeoutError:
        if llm_client is None:
            return JSONResponse(status_code=500, content={"error": "검사가 예상보다 오래 걸려요. 잠시 후 다시 시도해주세요."})
        # 남은 호출을 끊는다 — 응답 후에도 유료 호출이 이어지지 않게 (in-flight 1건은
        # SDK 타임아웃으로 유한 시간 내 종료)
        llm_client.cancel()
        skipped_reason = "llm_error"
        # 이미 외부 호출이 시작됐다면 비용이 발생했으므로 환불하지 않는다
        if consumed and llm_client.calls_started == 0:
            do_refund()
        elif consumed:
            logger.warning("타임아웃 — 외부 호출 %d회 발생으로 환불 없음", llm_client.calls_started)
        try:
            outcome = await _run_bounded(_lint_executor, run_lint, source, timeout=RULES_TIMEOUT)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=503, content={"error": "검사가 예상보다 오래 걸려요. 잠시 후 다시 시도해주세요."},
                                headers={"Retry-After": "30"})

    if outcome.llm_error:
        if consumed and (llm_client is None or llm_client.calls_started == 0):
            do_refund()
        skipped_reason = "llm_error"

    if llm_client is not None and llm_client.budget_exhausted:
        labels = {
            "unsupported-claim": "근거 없는 주장",
            "vague-goal": "구체성 부족",
            "logic-gap": "논리 단절",
            "internal-contradiction": "내부 모순",
            "enrich": "보강 제안",
        }
        limited = ", ".join(labels.get(n, n) for n in sorted(llm_client.limited))
        warnings = warnings + [
            f"문서가 길어 일부 검사({limited})는 앞부분까지만 수행했어요 — 뒷부분을 나눠서 다시 진단하면 더 정확해요"
        ]

    return {
        "findings": outcome.findings,
        "converted_text": source,
        "meta": {
            "llm_ran": outcome.llm_ran,
            "llm_skipped_reason": skipped_reason,
            "remaining_today": (quota.remaining_admin(settings.admin_daily) if admin
                               else quota.remaining(ip)),
            "conversion_warnings": warnings,
        },
    }


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
