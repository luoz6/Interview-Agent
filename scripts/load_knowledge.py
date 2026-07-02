from pathlib import Path
import sys


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
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        domain = infer_domain(path, content)
        source_type = "expert_benchmark" if "benchmarks" in path.parts else "theory"
        tags = [domain] if domain == "general" else [domain, "general"]
        chunks.append(
            KnowledgeChunk(
                chunk_id=path.stem,
                title=path.stem.replace("_", " ").title(),
                content=content,
                source_type=source_type,
                domain=domain,
                tags=tags,
                metadata={"source_path": str(path)},
            )
        )
    return chunks


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
