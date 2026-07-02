from pathlib import Path
import sys
import json


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore, get_knowledge_store


KNOWLEDGE_ROOT = Path("app/data/knowledge")


def iter_markdown_files() -> list[Path]:
    return sorted(KNOWLEDGE_ROOT.rglob("*.md"))


def build_chunks() -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for path in iter_markdown_files():
        raw = path.read_text(encoding="utf-8").strip()
        metadata, content = parse_front_matter(raw)
        if not content:
            continue
        domain = resolve_domain(metadata=metadata, path=path, content=content)
        source_type = resolve_source_type(metadata=metadata, path=path)
        tags = resolve_tags(metadata=metadata, domain=domain)
        chunks.append(
            KnowledgeChunk(
                chunk_id=path.stem,
                title=resolve_title(metadata=metadata, path=path),
                content=content,
                source_type=source_type,
                domain=domain,
                tags=tags,
                metadata=build_chunk_metadata(metadata=metadata, path=path),
            )
        )
    return chunks


def parse_front_matter(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---\n"):
        return {}, raw.strip()

    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, raw.strip()

    header, body = parts
    metadata: dict[str, object] = {}
    for line in header.splitlines()[1:]:
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = _parse_front_matter_value(value.strip())
    return metadata, body.strip()


def resolve_domain(*, metadata: dict[str, object], path: Path, content: str) -> str:
    domain = metadata.get("domain")
    if isinstance(domain, str) and domain.strip():
        return domain.strip()
    return infer_domain(path, content)


def resolve_source_type(*, metadata: dict[str, object], path: Path) -> str:
    source_type = metadata.get("source_type")
    if isinstance(source_type, str) and source_type.strip():
        return source_type.strip()
    return "expert_benchmark" if "benchmarks" in path.parts else "theory"


def resolve_tags(*, metadata: dict[str, object], domain: str) -> list[str]:
    tags = metadata.get("tags")
    if isinstance(tags, list):
        normalized = [str(tag).strip() for tag in tags if str(tag).strip()]
        if normalized:
            return normalized
    return [domain] if domain == "general" else [domain, "general"]


def resolve_title(*, metadata: dict[str, object], path: Path) -> str:
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return path.stem.replace("_", " ").title()


def build_chunk_metadata(*, metadata: dict[str, object], path: Path) -> dict[str, str]:
    chunk_metadata: dict[str, str] = {"source_path": str(path)}
    for key in ("domain", "source_type", "title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            chunk_metadata[key] = value.strip()
    return chunk_metadata


def infer_domain(path: Path, content: str) -> str:
    text = f"{path.stem}\n{content}".lower()
    if "fastapi" in text:
        return "fastapi"
    if "mysql" in text:
        return "mysql"
    if "kafka" in text:
        return "kafka"
    if "redis" in text:
        return "redis"
    if "system design" in text or "service scaling" in text or "scaling" in text:
        return "system-design"
    return "general"


def _parse_front_matter_value(value: str) -> object:
    if value.startswith("[") and value.endswith("]"):
        items = value[1:-1].strip()
        if not items:
            return []
        return [item.strip().strip("'\"") for item in items.split(",") if item.strip()]
    if value.startswith("{") and value.endswith("}"):
        return json.loads(value)
    return value.strip().strip("'\"")


def resolve_store(store: PgVectorKnowledgeStore | None = None) -> PgVectorKnowledgeStore:
    if store is not None:
        return store
    try:
        return get_knowledge_store()
    except KeyError as exc:
        raise RuntimeError("POSTGRES_DSN is required to load knowledge into pgvector") from exc


def load_knowledge(store: PgVectorKnowledgeStore | None = None) -> dict[str, int]:
    chunks = build_chunks()
    store = resolve_store(store)
    store.upsert_chunks(chunks)
    return {"discovered": len(chunks), "upserted": len(chunks)}


if __name__ == "__main__":
    summary = load_knowledge()
    print(
        f"Discovered {summary['discovered']} knowledge chunks and upserted "
        f"{summary['upserted']} rows."
    )
