import time
from collections.abc import Callable


class EvaluationRunner:
    def __init__(
        self,
        *,
        evaluator,
        artifact_store,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.evaluator = evaluator
        self.artifact_store = artifact_store
        self.sleep = sleep

    def run(
        self,
        *,
        dataset,
        runs_per_case: int,
        max_attempts: int | None = None,
    ) -> list[dict]:
        lookup = {case.case_id: case for case in dataset.cases}
        pending = self.artifact_store.pending_attempts(
            list(lookup),
            runs_per_case=runs_per_case,
        )
        if max_attempts is not None:
            pending = pending[:max_attempts]

        completed: list[dict] = []
        for case_id, run_number in pending:
            case = lookup[case_id]
            session_id = f"stage40-{case_id}-{run_number}"
            trace_dir = self.artifact_store.attempt_directory(case_id, run_number)
            try:
                normalized = self._evaluate_with_retry(
                    case,
                    session_id=session_id,
                    run_number=run_number,
                    trace_dir=trace_dir,
                )
            except Exception as exc:
                if _is_budget_exhausted(exc):
                    raise
                self.artifact_store.write_error(
                    case_id,
                    run_number,
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                continue
            self.artifact_store.write_attempt(
                case_id,
                run_number,
                normalized=normalized,
            )
            completed.append(normalized)
        return completed

    def _evaluate_with_retry(self, case, *, session_id, run_number, trace_dir):
        for attempt in range(3):
            try:
                normalized = self.evaluator.evaluate_case(
                    case,
                    session_id=session_id,
                    run_number=run_number,
                    trace_dir=trace_dir,
                )
                if normalized.get("fallback") and attempt < 2:
                    self.sleep(min(2**attempt, 4))
                    continue
                return normalized
            except Exception as exc:
                if _is_budget_exhausted(exc):
                    raise
                if attempt == 2 or not _is_transient(exc):
                    raise
                self.sleep(min(2**attempt, 4))
        raise AssertionError("unreachable")


def _is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "timeout",
            "rate limit",
            "429",
            "temporarily unavailable",
            "connection",
        )
    )


def _is_budget_exhausted(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "ProviderInvocationBudgetExhausted":
            return True
        current = current.__cause__ or current.__context__
    return False
