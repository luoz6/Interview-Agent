from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Protocol, runtime_checkable

from app.services.context_artifacts import canonical_json


_SENSITIVE_SCOPE = re.compile(
    r"(?:^[Bb]earer(?:[ .:]|$)|^sk-[A-Za-z0-9_-]{8,}|://|"
    r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$)"
)


@runtime_checkable
class ContextArtifactPrivacyScopeResolver(Protocol):
    def for_prep(
        self,
        *,
        deployment_scope: str,
        principal_id: str | None,
    ) -> str:
        """Return canonical non-secret security-domain material for Prep."""

    def for_interview(
        self,
        *,
        deployment_scope: str,
        session_id: str,
    ) -> str:
        """Return canonical deployment-and-session-bound scope material."""

    def for_review(
        self,
        *,
        deployment_scope: str,
        session_id: str,
    ) -> str:
        """Return canonical Review scope material excluding job/attempt IDs."""


class StableContextArtifactPrivacyScopeResolver:
    """Initial single-tenant-per-deployment Stage 50 scope policy."""

    def for_prep(
        self,
        *,
        deployment_scope: str,
        principal_id: str | None,
    ) -> str:
        material = {
            "scope_version": "context-artifact-privacy-v1",
            "boundary": "prep",
            "deployment_scope": self._validate_scope_value(deployment_scope),
            "principal_id": (
                self._validate_scope_value(principal_id)
                if principal_id is not None
                else None
            ),
        }
        return canonical_json(material)

    def for_interview(
        self,
        *,
        deployment_scope: str,
        session_id: str,
    ) -> str:
        return self._session_scope(
            boundary="interview",
            deployment_scope=deployment_scope,
            session_id=session_id,
        )

    def for_review(
        self,
        *,
        deployment_scope: str,
        session_id: str,
    ) -> str:
        return self._session_scope(
            boundary="review",
            deployment_scope=deployment_scope,
            session_id=session_id,
        )

    def _session_scope(
        self,
        *,
        boundary: str,
        deployment_scope: str,
        session_id: str,
    ) -> str:
        return canonical_json(
            {
                "scope_version": "context-artifact-privacy-v1",
                "boundary": boundary,
                "deployment_scope": self._validate_scope_value(deployment_scope),
                "session_id": self._validate_scope_value(session_id),
            }
        )

    @staticmethod
    def _validate_scope_value(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("privacy scope material must be non-empty")
        resolved = value.strip()
        if len(resolved) > 256 or _SENSITIVE_SCOPE.search(resolved):
            raise ValueError("privacy scope material is not a trusted identifier")
        return resolved



def privacy_scope_sha256(canonical_scope_material: str) -> str:
    if not isinstance(canonical_scope_material, str) or not canonical_scope_material:
        raise ValueError("canonical privacy scope material must be non-empty")
    try:
        parsed = json.loads(canonical_scope_material)
    except (TypeError, ValueError) as exc:
        raise ValueError("privacy scope material must be canonical JSON") from exc
    if not isinstance(parsed, dict) or canonical_json(parsed) != canonical_scope_material:
        raise ValueError("privacy scope material must be canonical JSON")
    return sha256(canonical_scope_material.encode("utf-8")).hexdigest()
