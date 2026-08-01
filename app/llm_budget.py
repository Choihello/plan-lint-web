from __future__ import annotations

import logging
import threading

logger = logging.getLogger("planlint.web")


class LLMCancelled(Exception):
    """요청이 타임아웃돼 남은 LLM 호출을 중단한다 (유료 호출 유출 방지)."""


class BudgetedClient:
    """요청 하나가 쓸 수 있는 LLM 호출 수를 강제하고, 취소 시 남은 호출을 끊는다.

    왜 필요한가: 엔진의 per-section 체커(unsupported-claim·vague-goal)는 프로파일
    섹션이 아니라 **문서의 모든 헤딩 섹션**을 순회한다. 소제목이 20개인 문서는
    실측 42회를 호출했다 — 요청당 비용에 사실상 상한이 없다.

    예산 소진은 '오류'가 아니라 '정직한 부분 검사'로 처리한다: 남은 호출은 결함
    0건("[]")으로 응답해 이미 계산된 결과를 살리고, budget_exhausted로 고지한다.
    취소는 반대로 예외를 던져 즉시 중단한다.
    """

    def __init__(self, inner, max_calls: int):
        self._inner = inner
        self._max = max_calls
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self.calls_started = 0
        self.budget_exhausted = False

    # 엔진이 사용량 보고에 접근할 수 있게 위임
    def __getattr__(self, name):
        return getattr(self._inner, name)

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def complete(self, system: str, user: str) -> str:
        if self._cancelled.is_set():
            raise LLMCancelled("요청이 취소돼 LLM 호출을 중단했습니다.")
        with self._lock:
            if self.calls_started >= self._max:
                if not self.budget_exhausted:
                    logger.warning("LLM 호출 예산 소진: %d회", self._max)
                self.budget_exhausted = True
                return "[]"
            self.calls_started += 1
        return self._inner.complete(system, user)


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
