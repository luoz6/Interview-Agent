import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_PROSE_TOKEN_RE = re.compile(
    r"[A-Za-z]+(?:-[A-Za-z]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+"
)
_FENCE_LINE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<suffix>.*)$")
_CLOSING_FENCE_LINE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_URL_RE = re.compile(r"https?://[^\s<>\])}]+", flags=re.IGNORECASE)
_ALLOWED_TECHNICAL_TERMS = {
    "python",
    "fastapi",
    "redis",
    "mysql",
    "postgresql",
    "kafka",
    "rocketmq",
    "sql",
    "http",
    "https",
    "asgi",
    "cache-aside",
}
_MIN_CHINESE_CHARACTERS = 300
_MAX_CHINESE_CHARACTERS = 1200


class DuplicateFrontMatterKeyError(ValueError):
    """Raised when a YAML mapping repeats a key."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise DuplicateFrontMatterKeyError(f"duplicate front matter key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class KnowledgeReferenceV2(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    url: AnyHttpUrl
    source_kind: Literal["official_cn", "secondary_cn"]
    publisher: str = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def validate_chinese_title(cls, value: str) -> str:
        if not _CJK_RE.search(value):
            raise ValueError("reference title must contain Chinese characters")
        return value

    @field_validator("url")
    @classmethod
    def validate_https_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("reference URL must use HTTPS")
        return value


class KnowledgeMetadataV2(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    title: str = Field(min_length=1)
    domain: Literal[
        "python",
        "fastapi",
        "redis",
        "mysql",
        "postgresql",
        "kafka",
        "rocketmq",
        "system-design",
        "reliability",
    ]
    source_type: Literal["theory", "engineering_guide", "expert_benchmark"]
    content_kind: Literal[
        "mechanism",
        "failure_mode",
        "engineering_practice",
        "benchmark",
        "hard_negative",
    ]
    tags: list[str] = Field(min_length=2)
    aliases: list[str] = Field(min_length=1, max_length=8)
    technical_terms: list[str] = Field(default_factory=list, max_length=12)
    topic: str = ""
    metadata_schema_version: Literal["knowledge-metadata-v2.1"] = (
        "knowledge-metadata-v2.1"
    )
    difficulty: Literal["beginner", "intermediate", "advanced"]
    question_patterns: list[str] = Field(min_length=2, max_length=5)
    references: list[KnowledgeReferenceV2] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def validate_chinese_title(cls, value: str) -> str:
        if not _CJK_RE.search(value):
            raise ValueError("metadata title must contain Chinese characters")
        return value

    @field_validator("question_patterns")
    @classmethod
    def validate_chinese_questions(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or not _CJK_RE.search(value) for value in values):
            raise ValueError("question patterns must contain Chinese characters")
        return values

    @field_validator("tags", "aliases", "technical_terms")
    @classmethod
    def validate_nonempty_unique_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("metadata lists must not contain empty values")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("metadata lists must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_metadata_contract(self):
        if not self.topic:
            self.topic = self.id.replace("_", "-")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", self.topic):
            raise ValueError("topic must be a stable lowercase identifier")
        if self.domain.casefold() not in {tag.casefold() for tag in self.tags}:
            raise ValueError("tags must include the domain tag")

        normalized_urls = [str(reference.url) for reference in self.references]
        if len(set(normalized_urls)) != len(normalized_urls):
            raise ValueError("duplicate reference URL")

        if any(reference.source_kind == "official_cn" for reference in self.references):
            return self

        secondary = [
            reference
            for reference in self.references
            if reference.source_kind == "secondary_cn"
        ]
        publishers = {reference.publisher.casefold() for reference in secondary}
        hostnames = {
            (urlsplit(str(reference.url)).hostname or "").casefold()
            for reference in secondary
        }
        if len(secondary) < 2 or len(publishers) < 2 or len(hostnames) < 2:
            raise ValueError(
                "two independent Chinese sources with distinct publishers and hosts "
                "are required when no official Chinese source exists"
            )
        return self


class KnowledgeDocumentV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: KnowledgeMetadataV2
    body: str
    chinese_character_count: int


def strip_non_prose_markdown(text: str) -> str:
    prose_lines: list[str] = []
    fence_character: str | None = None
    opening_fence_length = 0
    for line in text.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        if fence_character is None:
            opening = _FENCE_LINE_RE.match(candidate)
            if opening is not None:
                fence = opening.group("fence")
                suffix = opening.group("suffix")
                if fence.startswith("`") and "`" in suffix:
                    prose_lines.append(line)
                    continue
                fence_character = fence[0]
                opening_fence_length = len(fence)
                prose_lines.append("\n" if line.endswith(("\n", "\r")) else " ")
                continue
            prose_lines.append(line)
            continue

        closing = _CLOSING_FENCE_LINE_RE.match(candidate)
        if closing is not None:
            fence = closing.group("fence")
            if fence[0] == fence_character and len(fence) >= opening_fence_length:
                fence_character = None
                opening_fence_length = 0
        prose_lines.append("\n" if line.endswith(("\n", "\r")) else " ")

    stripped = "".join(prose_lines)
    stripped = _INLINE_CODE_RE.sub(" ", stripped)
    return _URL_RE.sub(" ", stripped)


def _contains_disallowed_english_prose(text: str) -> bool:
    consecutive_words = 0
    for match in _PROSE_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _CJK_RE.search(token):
            consecutive_words = 0
            continue
        if token.casefold() in _ALLOWED_TECHNICAL_TERMS:
            continue
        consecutive_words += 1
        if consecutive_words >= 4:
            return True
    return False


def _split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("document must start with explicit YAML front matter")
    closing_delimiter = text.find("\n---\n", 4)
    if closing_delimiter < 0:
        raise ValueError("document front matter must have a closing delimiter")
    front_matter = text[4:closing_delimiter]
    body = text[closing_delimiter + 5 :]
    if not front_matter.strip():
        raise ValueError("document front matter must not be empty")
    return front_matter, body


def load_knowledge_document_v2(path: Path) -> KnowledgeDocumentV2:
    front_matter, body = _split_front_matter(path.read_text(encoding="utf-8"))
    metadata_payload = yaml.load(front_matter, Loader=_UniqueKeySafeLoader)
    if not isinstance(metadata_payload, dict):
        raise ValueError("document front matter must be a YAML mapping")

    metadata = KnowledgeMetadataV2.model_validate(metadata_payload)
    prose = strip_non_prose_markdown(body)
    chinese_character_count = len(_CJK_RE.findall(prose))
    if not _MIN_CHINESE_CHARACTERS <= chinese_character_count <= _MAX_CHINESE_CHARACTERS:
        raise ValueError(
            "body must contain between 300 and 1200 Chinese characters after "
            "removing code and URLs"
        )
    if _contains_disallowed_english_prose(prose):
        raise ValueError("body contains disallowed English prose")

    return KnowledgeDocumentV2(
        metadata=metadata,
        body=body.strip(),
        chinese_character_count=chinese_character_count,
    )
