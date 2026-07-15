from pathlib import Path
import sys
import json


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore, get_knowledge_store


KNOWLEDGE_ROOT = Path("app/data/knowledge")


def iter_markdown_files(knowledge_root: Path | str = KNOWLEDGE_ROOT) -> list[Path]:
    return sorted(Path(knowledge_root).rglob("*.md"))


def build_chunks(knowledge_root: Path | str = KNOWLEDGE_ROOT) -> list[KnowledgeChunk]:
    from scripts.build_knowledge_manifest import build_manifest

    root = Path(knowledge_root)
    manifest = build_manifest(root)
    manifest_by_id = {item["chunk_id"]: item for item in manifest["chunks"]}
    chunks: list[KnowledgeChunk] = []
    for path in iter_markdown_files(root):
        raw = path.read_text(encoding="utf-8").strip()
        metadata, content = parse_front_matter(raw)
        if not content:
            continue
        chunk_id = resolve_chunk_id(metadata=metadata, path=path)
        domain = resolve_domain(metadata=metadata, path=path, content=content)
        source_type = resolve_source_type(metadata=metadata, path=path)
        tags = resolve_tags(metadata=metadata, domain=domain)
        manifest_entry = manifest_by_id[chunk_id]
        chunk_metadata = build_chunk_metadata(
            metadata=metadata,
            path=path,
            knowledge_root=root,
        )
        chunk_metadata.update(
            {
                "content_sha256": manifest_entry["content_sha256"],
                "corpus_manifest_sha256": manifest["corpus_manifest_sha256"],
                "corpus_version": manifest["corpus_version"],
                "content_kind": manifest_entry["content_kind"],
            }
        )
        chunks.append(
            KnowledgeChunk(
                chunk_id=chunk_id,
                title=resolve_title(metadata=metadata, path=path),
                content=content,
                source_type=source_type,
                domain=domain,
                tags=tags,
                metadata=chunk_metadata,
            )
        )
    return chunks


def resolve_chunk_id(*, metadata: dict[str, object], path: Path) -> str:
    chunk_id = metadata.get("id")
    if isinstance(chunk_id, str) and chunk_id.strip():
        return chunk_id.strip()
    return path.stem


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


def build_chunk_metadata(
    *,
    metadata: dict[str, object],
    path: Path,
    knowledge_root: Path | str = KNOWLEDGE_ROOT,
) -> dict[str, str]:
    root = Path(knowledge_root)
    chunk_metadata: dict[str, str] = {
        "source_path": path.relative_to(root).as_posix()
    }
    for key in ("id", "domain", "source_type", "content_kind", "title"):
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
