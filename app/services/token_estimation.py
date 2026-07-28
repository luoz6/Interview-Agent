from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence, Mapping


class ContextEstimatorUnavailable(RuntimeError):
    """No configured estimator can safely measure provider input."""


class TokenEstimator(Protocol):
    def estimate_text(self, text: str, *, model: str) -> int:
        ...

    def estimate_messages(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str,
    ) -> int:
        ...


class TikTokenEstimator:
    def __init__(self, *, encoding_name: str | None = None) -> None:
        self.encoding_name = encoding_name

    def _encoding(self, model: str):
        import tiktoken

        if self.encoding_name:
            return tiktoken.get_encoding(self.encoding_name)
        return tiktoken.encoding_for_model(model)

    def estimate_text(self, text: str, *, model: str) -> int:
        return len(self._encoding(model).encode(text))

    def estimate_messages(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str,
    ) -> int:
        rendered = "\n".join(
            f"{message.get('role', '')}: {message.get('content', '')}"
            for message in messages
        )
        return self.estimate_text(rendered, model=model)


class ConservativeUtf8TokenEstimator:
    """A deliberately conservative fallback for unknown tokenizers."""

    def estimate_text(self, text: str, *, model: str) -> int:
        del model
        return len(text.encode("utf-8"))

    def estimate_messages(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str,
    ) -> int:
        return self.estimate_text(
            "\n".join(
                f"{message.get('role', '')}: {message.get('content', '')}"
                for message in messages
            ),
            model=model,
        )


EstimatorPath = Literal[
    "exact_model",
    "configured_family",
    "tested_family_mapping",
    "conservative_utf8",
]


@dataclass(frozen=True)
class TokenEstimatorResolution:
    estimator: TokenEstimator
    estimator_path: EstimatorPath
    fallback_used: bool


class CompositeTokenEstimator:
    """Resolve the Stage 49 explicit fail-closed estimator chain."""

    def __init__(
        self,
        *,
        configured_family: str | None = None,
        tested_family_mappings: Mapping[str, str] | None = None,
        conservative: TokenEstimator | None = None,
    ) -> None:
        self.configured_family = configured_family
        self.tested_family_mappings = dict(tested_family_mappings or {})
        self.conservative = conservative or ConservativeUtf8TokenEstimator()

    def resolve(self, *, model: str) -> TokenEstimatorResolution:
        try:
            estimator = TikTokenEstimator()
            estimator.estimate_text("", model=model)
            return TokenEstimatorResolution(estimator, "exact_model", False)
        except Exception:
            pass

        if self.configured_family:
            try:
                estimator = TikTokenEstimator(encoding_name=self.configured_family)
                estimator.estimate_text("", model=model)
                return TokenEstimatorResolution(
                    estimator,
                    "configured_family",
                    True,
                )
            except Exception:
                pass

        mapped_family = self.tested_family_mappings.get(model)
        if mapped_family:
            try:
                estimator = TikTokenEstimator(encoding_name=mapped_family)
                estimator.estimate_text("", model=model)
                return TokenEstimatorResolution(
                    estimator,
                    "tested_family_mapping",
                    True,
                )
            except Exception:
                pass

        try:
            self.conservative.estimate_text("", model=model)
            return TokenEstimatorResolution(
                self.conservative,
                "conservative_utf8",
                True,
            )
        except Exception as exc:
            raise ContextEstimatorUnavailable(
                "no token estimator is available"
            ) from exc
