from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import load_knowledge


CORPUS_VERSION = "stage42-v1"
DEFAULT_OUTPUT_PATH = load_knowledge.KNOWLEDGE_ROOT / "manifest.json"


def build_manifest(
    knowledge_root: Path | str = load_knowledge.KNOWLEDGE_ROOT,
    *,
    corpus_version: str = CORPUS_VERSION,
) -> dict:
    root = Path(knowledge_root)
    entries: list[dict] = []
    seen_ids: set[str] = set()
    seen_content: dict[str, str] = {}

    for path in load_knowledge.iter_markdown_files(root):
        raw = path.read_text(encoding="utf-8").strip()
        metadata, content = load_knowledge.parse_front_matter(raw)
        if not content:
            continue
        chunk_id = load_knowledge.resolve_chunk_id(metadata=metadata, path=path)
        if chunk_id in seen_ids:
            raise ValueError(f"duplicate knowledge chunk id: {chunk_id}")
        seen_ids.add(chunk_id)

        duplicate_key = _normalized_content_fingerprint(content)
        if duplicate_key in seen_content:
            raise ValueError(
                "duplicate knowledge content: "
                f"{seen_content[duplicate_key]} and {chunk_id}"
            )
        seen_content[duplicate_key] = chunk_id

        domain = load_knowledge.resolve_domain(
            metadata=metadata,
            path=path,
            content=content,
        )
        entries.append(
            {
                "chunk_id": chunk_id,
                "content_kind": _required_metadata(metadata, "content_kind"),
                "content_sha256": content_sha256(content),
                "domain": domain,
                "source_path": path.relative_to(root).as_posix(),
                "source_type": load_knowledge.resolve_source_type(
                    metadata=metadata,
                    path=path,
                ),
                "tags": load_knowledge.resolve_tags(metadata=metadata, domain=domain),
                "title": load_knowledge.resolve_title(metadata=metadata, path=path),
            }
        )

    entries.sort(key=lambda item: item["chunk_id"])
    manifest_payload = {
        "corpus_version": corpus_version,
        "chunk_count": len(entries),
        "chunks": entries,
    }
    manifest_hash = hashlib.sha256(
        json.dumps(
            manifest_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "manifest_schema_version": 1,
        **manifest_payload,
        "corpus_manifest_sha256": manifest_hash,
    }


def write_manifest(
    *,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    knowledge_root: Path | str = load_knowledge.KNOWLEDGE_ROOT,
    corpus_version: str = CORPUS_VERSION,
) -> dict:
    manifest = build_manifest(knowledge_root, corpus_version=corpus_version)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def content_sha256(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalized_content_fingerprint(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _required_metadata(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"knowledge chunk requires {key} metadata")
    return value.strip()


if __name__ == "__main__":
    generated = write_manifest()
    print(
        f"Wrote {generated['chunk_count']} chunks with corpus hash "
        f"{generated['corpus_manifest_sha256']}."
    )
