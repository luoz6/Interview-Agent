KEYWORD_TAGS = [
    "python",
    "fastapi",
    "redis",
    "postgresql",
    "mysql",
    "java",
    "spring",
    "kafka",
    "rabbitmq",
    "system-design",
]


def extract_job_tags(job_description: str) -> list[str]:
    text = job_description.lower()
    tags: list[str] = []
    for tag in KEYWORD_TAGS:
        if tag in text and tag not in tags:
            tags.append(tag)
    return tags or ["general"]
