from __future__ import annotations

import re
from typing import Any, Iterable

from contracts.evidence.canonical import normalize_canonical_value


SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "client_secret",
        "credential",
        "dsn",
        "password",
        "private_key",
        "provider_key",
        "refresh_token",
        "secret",
    }
)
_CREDENTIAL_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.I)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


class PrivacyViolation(ValueError):
    def __init__(self, paths: Iterable[str]):
        self.paths = tuple(sorted(set(paths)))
        super().__init__("sensitive evidence fields found: " + ", ".join(self.paths))


def sensitive_paths(value: Any) -> tuple[str, ...]:
    normalized = normalize_canonical_value(value)
    findings: list[str] = []

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else key
                normalized_key = key.casefold().replace("-", "_")
                if normalized_key in SENSITIVE_KEYS:
                    findings.append(child_path)
                visit(child, child_path)
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
            return
        if isinstance(item, str) and (
            _CREDENTIAL_URL.search(item) or _PRIVATE_KEY.search(item)
        ):
            findings.append(path or "$value")

    visit(normalized, "")
    return tuple(sorted(set(findings)))


def assert_privacy_safe(value: Any) -> None:
    findings = sensitive_paths(value)
    if findings:
        raise PrivacyViolation(findings)
