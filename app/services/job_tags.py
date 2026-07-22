TAG_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python", ("python",)),
    ("fastapi", ("fastapi",)),
    ("redis", ("redis",)),
    ("postgresql", ("postgresql",)),
    ("mysql", ("mysql",)),
    ("java", ("java",)),
    ("spring", ("spring",)),
    ("kafka", ("kafka",)),
    ("rabbitmq", ("rabbitmq",)),
    ("system-design", ("system-design", "系统设计")),
    ("reliability", ("reliability", "稳定性", "可靠性", "可观测性", "容量规划")),
)

KEYWORD_TAGS = [tag for tag, _aliases in TAG_ALIASES]


def extract_job_tags(job_description: str) -> list[str]:
    text = job_description.lower()
    tags: list[str] = []
    for tag, aliases in TAG_ALIASES:
        if any(alias in text for alias in aliases) and tag not in tags:
            tags.append(tag)
    return tags or ["general"]
