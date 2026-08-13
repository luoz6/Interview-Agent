from __future__ import annotations

import re
import json
from pathlib import Path

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
    "rocketmq": {"label": "RocketMQ", "domain": "messaging"},
    "rabbitmq": {"label": "RabbitMQ", "domain": "messaging"},
    "system-design": {"label": "系统设计", "domain": "system-design"},
    "reliability": {"label": "可靠性", "domain": "可靠性"},
}

# These tags are present in the current corpus metadata. Task 2B will derive the
# same capability from the versioned corpus manifest.
LEGACY_KNOWLEDGE_COVERED_TAGS = {
    "python",
    "fastapi",
    "redis",
    "mysql",
    "postgresql",
    "rocketmq",
    "system-design",
    "reliability",
}
P1_REQUIRED_COVERED_TAGS = frozenset(LEGACY_KNOWLEDGE_COVERED_TAGS)
P1_MINIMUM_EVIDENCE_COUNT = 2


def derive_covered_tags_from_manifest(manifest: dict) -> set[str]:
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        return set(LEGACY_KNOWLEDGE_COVERED_TAGS)
    if coverage.get("schema_version") != "knowledge-coverage-v1":
        raise ValueError("unsupported knowledge coverage schema")
    tags = coverage.get("canonical_tags")
    counts = coverage.get("evidence_class_counts")
    required = coverage.get("minimum_evidence_classes")
    if not isinstance(tags, list) or not isinstance(counts, dict):
        raise ValueError("knowledge coverage metadata is invalid")
    if required != ["positive", "negative", "boundary"]:
        raise ValueError("knowledge coverage evidence classes are invalid")
    result = set()
    for tag in tags:
        values = counts.get(tag)
        if (
            tag not in CANONICAL_TAXONOMY
            or not isinstance(values, dict)
            or any(
                not isinstance(values.get(name), int)
                or values[name] < P1_MINIMUM_EVIDENCE_COUNT
                for name in required
            )
        ):
            raise ValueError("knowledge coverage minimum is unavailable")
        result.add(tag)
    if not result:
        raise ValueError("knowledge coverage contains no approved tags")
    return result


def load_active_knowledge_covered_tags(
    manifest_path: Path | str = Path("app/data/knowledge_v2/manifest.json"),
) -> set[str]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return derive_covered_tags_from_manifest(manifest)

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
    ("senior", re.compile(r"高级|资深")),
    ("mid", re.compile(r"中级")),
    ("junior", re.compile(r"初级")),
)
_CHINESE_ROLE_PATTERN = re.compile(
    r"(?:(?:高级|资深|中级|初级))?(?:后端|前端|全栈|软件|平台|数据|算法|测试|运维|云|安全)(?:工程师|开发|架构师)"
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
    covered_tags = load_active_knowledge_covered_tags()
    uncovered = [
        CANONICAL_TAXONOMY[tag]["label"]
        for tag in canonical_tags
        if tag not in covered_tags and tag in CANONICAL_TAXONOMY
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
        chinese_match = _CHINESE_ROLE_PATTERN.search(text)
        return chinese_match.group(0) if chinese_match else ""
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
