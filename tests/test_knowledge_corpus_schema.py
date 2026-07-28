from pathlib import Path

import pytest

from app.services.knowledge_corpus_schema import (
    DuplicateFrontMatterKeyError,
    load_knowledge_document_v2,
    strip_non_prose_markdown,
)


def valid_document() -> str:
    body = """# Redis 缓存一致性的边界

## 核心结论
更新数据库后删除缓存是常见基线，但仍需处理并发读写窗口和删除失败。

## 机制与边界
这里说明适用条件、并发边界、失败补偿、监控指标以及逐步降级的方法。

## 常见错误
不能把延迟双删当成绝对一致，也不能忽略重试任务的幂等性和时序风险。

## 工程权衡
方案需要在一致性、可用性、实现复杂度和数据库压力之间进行明确取舍。

## 可观察评分信号
回答应说明更新顺序、失败补偿、并发窗口、监控指标和降级策略。
"""
    body += "中文技术说明覆盖机制边界故障处理监控指标容量影响与工程取舍。" * 12
    return f"""---
id: redis_consistency
title: Redis 缓存一致性的边界
domain: redis
source_type: theory
content_kind: mechanism
tags: [redis, 缓存, 一致性]
aliases: [缓存一致性, Cache-Aside]
difficulty: intermediate
question_patterns:
  - 缓存与数据库如何保持一致？
  - 为什么通常先更新数据库再删除缓存？
references:
  - title: Redis 中文资料
    url: https://example.cn/redis-consistency
    source_kind: official_cn
    publisher: Redis 中文站
---
{body}
"""


def write_document(tmp_path: Path, text: str, name: str = "document.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def replace_body(text: str, body: str) -> str:
    front_matter, _ = text.split("\n---\n", 1)
    return f"{front_matter}\n---\n{body}\n"


def test_v2_document_accepts_complete_chinese_metadata(tmp_path: Path):
    document = load_knowledge_document_v2(write_document(tmp_path, valid_document()))

    assert document.metadata.id == "redis_consistency"
    assert document.metadata.domain == "redis"
    assert document.metadata.references[0].source_kind == "official_cn"
    assert 300 <= document.chinese_character_count <= 1200


def test_v2_document_rejects_duplicate_yaml_keys(tmp_path: Path):
    text = valid_document().replace(
        "domain: redis", "domain: redis\ndomain: mysql"
    )

    with pytest.raises(DuplicateFrontMatterKeyError):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_secondary_sources_require_two_independent_publishers(tmp_path: Path):
    text = valid_document().replace("source_kind: official_cn", "source_kind: secondary_cn")

    with pytest.raises(ValueError, match="two independent Chinese sources"):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_two_independent_secondary_sources_are_accepted(tmp_path: Path):
    replacement = """source_kind: secondary_cn
    publisher: 中文技术社区甲
  - title: 缓存一致性实践
    url: https://example.org/cache-consistency
    source_kind: secondary_cn
    publisher: 中文技术社区乙"""
    text = valid_document().replace(
        "source_kind: official_cn\n    publisher: Redis 中文站", replacement
    )

    document = load_knowledge_document_v2(write_document(tmp_path, text))

    assert len(document.metadata.references) == 2


@pytest.mark.parametrize(
    "body",
    [
        "english sentence words are not allowed" + "中文" * 160,
        "短正文",
        "中文" * 601,
    ],
)
def test_v2_document_rejects_english_prose_and_body_outside_size_bounds(
    tmp_path: Path, body: str
):
    text = replace_body(valid_document(), body)

    with pytest.raises(ValueError):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_v2_document_rejects_unknown_metadata_field(tmp_path: Path):
    text = valid_document().replace(
        "difficulty: intermediate", "difficulty: intermediate\nowner: internal"
    )

    with pytest.raises(ValueError):
        load_knowledge_document_v2(write_document(tmp_path, text))


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("domain: redis", "domain: mongodb"),
        ("source_type: theory", "source_type: blog"),
        ("content_kind: mechanism", "content_kind: tutorial"),
        ("difficulty: intermediate", "difficulty: expert"),
    ],
)
def test_v2_document_rejects_invalid_enums(
    tmp_path: Path, original: str, replacement: str
):
    text = valid_document().replace(original, replacement)

    with pytest.raises(ValueError):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_v2_document_requires_domain_tag(tmp_path: Path):
    text = valid_document().replace("tags: [redis, 缓存, 一致性]", "tags: [缓存, 一致性]")

    with pytest.raises(ValueError, match="domain tag"):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_v2_document_rejects_non_https_and_duplicate_reference_urls(tmp_path: Path):
    non_https = valid_document().replace("https://example.cn", "http://example.cn")
    with pytest.raises(ValueError, match="HTTPS"):
        load_knowledge_document_v2(write_document(tmp_path, non_https, "http.md"))

    duplicate = valid_document().replace(
        "    publisher: Redis 中文站",
        """    publisher: Redis 中文站
  - title: Redis 缓存实践
    url: https://example.cn/redis-consistency
    source_kind: official_cn
    publisher: Redis 官方社区""",
    )
    with pytest.raises(ValueError, match="duplicate reference URL"):
        load_knowledge_document_v2(write_document(tmp_path, duplicate, "duplicate-url.md"))


@pytest.mark.parametrize(
    ("second_url", "second_publisher"),
    [
        ("https://example.org/cache-consistency", "中文技术社区甲"),
        ("https://example.cn/cache-consistency", "中文技术社区乙"),
    ],
)
def test_secondary_sources_require_distinct_publishers_and_hosts(
    tmp_path: Path, second_url: str, second_publisher: str
):
    replacement = f"""source_kind: secondary_cn
    publisher: 中文技术社区甲
  - title: 缓存一致性实践
    url: {second_url}
    source_kind: secondary_cn
    publisher: {second_publisher}"""
    text = valid_document().replace(
        "source_kind: official_cn\n    publisher: Redis 中文站", replacement
    )

    with pytest.raises(ValueError, match="two independent Chinese sources"):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_code_blocks_inline_code_and_urls_do_not_count_as_chinese_prose(tmp_path: Path):
    excluded = """
```text
伪造中文字符""" + "中文" * 160 + """
```
`内联中文字符` https://example.cn/中文中文中文
"""
    text = replace_body(valid_document(), "有效正文" + excluded)

    with pytest.raises(ValueError, match="Chinese characters"):
        load_knowledge_document_v2(write_document(tmp_path, text))

    stripped = strip_non_prose_markdown(excluded)
    assert "伪造中文字符" not in stripped
    assert "内联中文字符" not in stripped
    assert "example.cn" not in stripped


def test_longer_commonmark_closing_fence_is_removed(tmp_path: Path):
    prose = "有效正文" * 75
    fenced = "\n```text\n" + "围栏中的中文" * 80 + "\n````\n"
    text = replace_body(valid_document(), prose + fenced)

    document = load_knowledge_document_v2(write_document(tmp_path, text))

    assert document.chinese_character_count == 300
    assert "围栏中的中文" not in strip_non_prose_markdown(fenced)


def test_whitelisted_technical_term_does_not_hide_english_prose(tmp_path: Path):
    body = "中文" * 150 + " We should use Redis cluster safely today"
    text = replace_body(valid_document(), body)

    with pytest.raises(ValueError, match="English prose"):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_pure_technical_identifiers_are_allowed(tmp_path: Path):
    body = "中文" * 150 + "\nPython FastAPI Redis MySQL PostgreSQL Kafka SQL HTTP HTTPS ASGI Cache-Aside"
    text = replace_body(valid_document(), body)

    document = load_knowledge_document_v2(write_document(tmp_path, text))

    assert document.chinese_character_count == 300


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("title: Redis 缓存一致性的边界", "title: Redis Cache-Aside"),
        ("  - 缓存与数据库如何保持一致？", "  - Redis Cache-Aside"),
        ("  - title: Redis 中文资料", "  - title: Redis Cache-Aside"),
    ],
)
def test_human_facing_metadata_requires_chinese(
    tmp_path: Path, original: str, replacement: str
):
    text = valid_document().replace(original, replacement, 1)

    with pytest.raises(ValueError, match="Chinese"):
        load_knowledge_document_v2(write_document(tmp_path, text))


def test_front_matter_must_be_explicitly_delimited(tmp_path: Path):
    text = valid_document().removeprefix("---\n")

    with pytest.raises(ValueError, match="front matter"):
        load_knowledge_document_v2(write_document(tmp_path, text))
