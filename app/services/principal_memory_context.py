import json

from app.services.principal_memory_sink_policy import (
    ASSISTANCE_CONTEXT_KIND,
    ASSISTANCE_LABEL,
    ASSISTANCE_WARNING,
)


class PrincipalMemoryContextRenderer:
    """Render a bounded, local-only follow-up assistance context block."""

    def __init__(self, *, config, estimator, model: str) -> None:
        self.config = config
        self.estimator = estimator
        self.model = model

    def render_bounded(self, selected):
        items = []
        best = ("", 0, 0)
        cap = self.config.long_term.max_local_consume_tokens
        for fact in selected:
            key, value = next(iter(json.loads(fact.normalized_fact).items()))
            items.append(
                f"- category={key}; value={value}; authority={fact.authority}; "
                "confirmation=user_confirmed; source_status=available"
            )
            block = self._render(items)
            tokens = self.estimator.estimate_text(block, model=self.model)
            if tokens > cap:
                break
            best = (block, len(items), tokens)
        return best

    @staticmethod
    def insert_before_current_candidate(base, block):
        candidate_index = next(
            (
                index
                for index in range(len(base) - 1, -1, -1)
                if base[index].get("role") == "candidate"
            ),
            None,
        )
        if candidate_index is None:
            return None
        current_candidate = base[candidate_index]
        preceding = base[:candidate_index] + base[candidate_index + 1 :]
        return [
            *preceding,
            {
                "role": "system",
                "content": block,
                "context_kind": ASSISTANCE_CONTEXT_KIND,
            },
            current_candidate,
        ]

    @staticmethod
    def _render(items):
        return "\n".join(
            [
                f"[{ASSISTANCE_LABEL}]",
                "Use: local follow-up assistance only.",
                ASSISTANCE_WARNING,
                *items,
                f"[/{ASSISTANCE_LABEL}]",
            ]
        )
