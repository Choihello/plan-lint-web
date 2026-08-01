from __future__ import annotations

import logging
import threading

logger = logging.getLogger("planlint.web")


class LLMCancelled(Exception):
    """요청이 타임아웃돼 남은 LLM 호출을 중단한다 (유료 호출 유출 방지)."""


class BudgetPool:
    """요청 하나가 쓸 수 있는 총 LLM 호출 수를 강제하고, 취소 시 남은 호출을 끊는다.

    왜 필요한가: 엔진의 per-section 체커(unsupported-claim·vague-goal)는 프로파일
    섹션이 아니라 **문서의 모든 헤딩 섹션**을 순회한다. 소제목 20개 문서는 실측
    42회를 호출했다 — 요청당 비용에 사실상 상한이 없다.

    왜 '몫(share)'이 필요한가: 총량만 막으면 먼저 도는 체커가 예산을 독식한다.
    실측(섹션 38개)에서 unsupported-claim이 24회를 전부 먹고 internal-contradiction과
    보강 제안이 아예 실행되지 않았다. 그래서 체커별로 몫을 나눠 배분한다.

    예산 소진은 '오류'가 아니라 '정직한 부분 검사'로 처리한다: 남은 호출은 결함
    0건("[]")으로 응답해 이미 계산된 결과를 살리고, 어떤 검사가 잘렸는지 고지한다.
    취소는 반대로 예외를 던져 즉시 중단한다.
    """

    def __init__(self, inner, total_calls: int):
        self._inner = inner
        self._total = total_calls
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self.calls_started = 0
        self.limited: set[str] = set()  # 예산 때문에 잘린 검사 이름

    # 엔진이 사용량 보고에 접근할 수 있게 위임
    def __getattr__(self, name):
        return getattr(self._inner, name)

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def budget_exhausted(self) -> bool:
        return bool(self.limited)

    def share(self, label: str, max_calls: int) -> "BudgetShare":
        """체커 하나에 배정할 몫. 총량과 자기 몫 둘 다 남아야 호출된다."""
        return BudgetShare(self, label, max_calls)

    def _run(self, label: str, system: str, user: str, share_ok: bool) -> str:
        if self._cancelled.is_set():
            raise LLMCancelled("요청이 취소돼 LLM 호출을 중단했습니다.")
        with self._lock:
            if not share_ok or self.calls_started >= self._total:
                if label not in self.limited:
                    logger.warning("LLM 예산으로 검사 축소: %s", label)
                self.limited.add(label)
                return "[]"
            self.calls_started += 1
        return self._inner.complete(system, user)

    # 몫 없이 직접 쓰는 경로(단순 사용)도 지원 — 총량만 적용
    def complete(self, system: str, user: str) -> str:
        return self._run("llm", system, user, share_ok=True)


class BudgetShare:
    """BudgetPool이 체커 하나에 배정한 호출 몫. 엔진에는 평범한 LLM 클라이언트로 보인다."""

    def __init__(self, pool: BudgetPool, label: str, max_calls: int):
        self._pool = pool
        self._label = label
        self._max = max_calls
        self._used = 0
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._pool, name)

    def complete(self, system: str, user: str) -> str:
        with self._lock:
            ok = self._used < self._max
            if ok:
                self._used += 1
        return self._pool._run(self._label, system, user, share_ok=ok)


def plan_shares(total: int, per_section_checkers: list[str], fixed: dict[str, int]) -> dict[str, int]:
    """총 예산을 체커별 몫으로 나눈다.

    문서 전체를 한 번만 보는 검사(fixed)는 적은 몫을 **먼저 확보**하고,
    섹션 수에 비례해 커지는 검사들이 남은 예산을 균등하게 나눈다.
    """
    shares = {}
    remaining = total
    for name, want in fixed.items():
        got = min(want, max(remaining, 0))
        shares[name] = got
        remaining -= got
    if per_section_checkers:
        each = max(remaining // len(per_section_checkers), 0)
        for name in per_section_checkers:
            shares[name] = each
    return shares


def apply_request_timeout(client, seconds: int):
    """공급자 SDK에 요청 타임아웃을 주입한다.

    openai/anthropic SDK 기본 타임아웃은 분 단위(openai 600초)라, 웹 요청 예산을
    한참 넘겨 좀비 호출로 남는다. 엔진(별도 저장소)을 고치지 않고 웹 계층에서
    SDK 클라이언트 옵션만 교체한다.
    """
    inner = getattr(client, "_client", None)
    if inner is None or not hasattr(inner, "with_options"):
        logger.warning("LLM SDK 타임아웃을 주입하지 못했습니다: %s", type(client).__name__)
        return client
    try:
        client._client = inner.with_options(timeout=seconds)
    except Exception as e:  # SDK 버전 차이로 실패해도 검사 자체는 진행
        logger.warning("LLM SDK 타임아웃 주입 실패: %s", type(e).__name__)
    return client
