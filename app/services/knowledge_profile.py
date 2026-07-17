from __future__ import annotations

import re

from app.services.job_tags import extract_job_tags
from app.services.prep import RoleProfile


CANONICAL_TAXONOMY: dict[str, dict[str, str]] = {
    "python": {"label": "Python", "domain": "backend"},
    "fastapi": {"label": "FastAPI", "domain": "backend"},
    "redis": {"label": "Redis", "domain": "cache"},
    "postgresql": {"label": "PostgreSQL", "domain": "database"},
    "mysql": {"label": "MySQL", "domain": "database"},
    "java": {"label": "Java", "domain": "backend"},
    "spring": {"label": "Spring", "domain": "backend"},
    "kafka": {"label": "Kafka", "domain": "messaging"},
    "rabbitmq": {"label": "RabbitMQ", "domain": "messaging"},
    "system-design": {"label": "System Design", "domain": "system-design"},
}

# These tags are present in the current corpus metadata. Task 2B will derive the
# same capability from the versioned corpus manifest.
KNOWLEDGE_COVERED_TAGS = {
    "python",
    "fastapi",
    "redis",
    "mysql",
    "kafka",
    "system-design",
}

_ROLE_PATTERN = re.compile(
    r"\b(?:(senior|sr\.?|junior|jr\.?|staff|principal|lead|mid(?:dle)?)[ -]+)?"
    r"((?:backend|front[ -]?end|full[ -]?stack|software|platform|data|"
    r"machine learning|ml|devops|cloud|security|qa|test)[ -]+"
    r"(?:engineer|developer|architect))\b",
    re.IGNORECASE,
)
_SENIORITY_PATTERNS = (
    ("principal", re.compile(r"\bprincipal\b", re.IGNORECASE)),
    ("staff", re.compile(r"\bstaff\b", re.IGNORECASE)),
    ("lead", re.compile(r"\b(?:lead|tech lead)\b", re.IGNORECASE)),
    ("senior", re.compile(r"\b(?:senior|sr\.?)\b", re.IGNORECASE)),
    ("mid", re.compile(r"\b(?:mid|middle)\b", re.IGNORECASE)),
    ("junior", re.compile(r"\b(?:junior|jr\.?)\b", re.IGNORECASE)),
)
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s()-]{7,}\d)(?!\w)")


def build_role_profile(job_description: str, resume_text: str) -> RoleProfile:
    normalized_jd = _normalize_multiline(job_description)
    normalized_resume = _normalize_multiline(resume_text)
    canonical_tags = _technical_tags(normalized_jd)
    resume_tags = set(_technical_tags(normalized_resume))

    domains = _dedupe(
        CANONICAL_TAXONOMY[tag]["domain"]
        for tag in canonical_tags
        if tag in CANONICAL_TAXONOMY
    )
    technologies = [
        CANONICAL_TAXONOMY[tag]["label"]
        for tag in canonical_tags
        if tag in CANONICAL_TAXONOMY
    ]
    resume_signals = [
        f"Resume mentions {CANONICAL_TAXONOMY[tag]['label']} experience."
        for tag in canonical_tags
        if tag in resume_tags and tag in CANONICAL_TAXONOMY
    ]
    uncovered = [
        CANONICAL_TAXONOMY[tag]["label"]
        for tag in canonical_tags
        if tag not in KNOWLEDGE_COVERED_TAGS and tag in CANONICAL_TAXONOMY
    ]

    return RoleProfile(
        role_title=_extract_role_title(normalized_jd),
        seniority=_extract_seniority(normalized_jd),
        canonical_tags=canonical_tags,
        domains=domains,
        technologies=technologies,
        responsibilities=_extract_responsibilities(normalized_jd),
        resume_signals=resume_signals,
        uncovered_technologies=uncovered,
        query_terms=_dedupe([*canonical_tags, *domains]),
    )


def _technical_tags(text: str) -> list[str]:
    if not text:
        return []
    return [tag for tag in extract_job_tags(text) if tag != "general"]


def _extract_role_title(text: str) -> str:
    match = _ROLE_PATTERN.search(text)
    if match is None:
        return ""
    seniority, role = match.groups()
    words = [word for word in (seniority, role) if word]
    title = " ".join(words).replace("-", " ")
    replacements = {"Ml": "ML", "Qa": "QA", "Devops": "DevOps"}
    return " ".join(replacements.get(word.title(), word.title()) for word in title.split())


def _extract_seniority(text: str) -> str:
    for value, pattern in _SENIORITY_PATTERNS:
        if pattern.search(text):
            return value
    return ""


def _extract_responsibilities(text: str) -> list[str]:
    if not text:
        return []
    title_match = _ROLE_PATTERN.search(text)
    responsibilities: list[str] = []
    for line in text.splitlines():
        sanitized = _sanitize_text(line).strip(" -\t")
        if not sanitized or (title_match and sanitized == title_match.group(0)):
            continue
        if sanitized not in responsibilities:
            responsibilities.append(sanitized[:160].rstrip())
        if len(responsibilities) == 5:
            break
    return responsibilities


def _sanitize_text(text: str) -> str:
    value = _URL_PATTERN.sub(" ", text)
    value = _EMAIL_PATTERN.sub(" ", value)
    value = _PHONE_PATTERN.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalize_multiline(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _dedupe(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
