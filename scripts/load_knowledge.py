from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.vector_store import KnowledgeChunk, get_knowledge_store


KNOWLEDGE_ROOT = Path("app/data/knowledge")


def iter_markdown_files() -> list[Path]:
    return sorted(KNOWLEDGE_ROOT.rglob("*.md"))


def build_chunks() -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for path in iter_markdown_files():
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        domain = "redis" if "redis" in path.name.lower() else "general"
        source_type = "expert_benchmark" if "benchmarks" in path.parts else "theory"
        chunks.append(
            KnowledgeChunk(
                chunk_id=path.stem,
                title=path.stem.replace("_", " ").title(),
                content=content,
                source_type=source_type,
                domain=domain,
                tags=[domain, "general"] if domain != "general" else ["general"],
                metadata={"source_path": str(path)},
            )
        )
    return chunks


def load_knowledge() -> int:
    chunks = build_chunks()
    try:
        store = get_knowledge_store()
    except KeyError as exc:
        raise RuntimeError("POSTGRES_DSN is required to load knowledge into pgvector") from exc
    store.upsert_chunks(chunks)
    return len(chunks)


if __name__ == "__main__":
    count = load_knowledge()
    print(f"Discovered and upserted {count} knowledge chunks.")
