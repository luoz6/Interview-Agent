from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from collections.abc import Callable
from typing import Any

from contracts.evidence.canonical import canonical_json
from contracts.evidence.digest import sha256_bytes
from contracts.evidence.envelope import EvidenceBundle


class AtomicEvidenceWriter:
    def __init__(
        self,
        *,
        post_write_verifier: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._post_write_verifier = post_write_verifier

    def write(self, path: Path, bundle: EvidenceBundle) -> str:
        target = path.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = (canonical_json(bundle) + "\n").encode("utf-8")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
            persisted = target.read_bytes()
            if persisted != payload:
                raise RuntimeError("post-write evidence verification failed")
            parsed = json.loads(persisted.decode("utf-8"))
            if self._post_write_verifier is not None:
                self._post_write_verifier(parsed)
            return sha256_bytes(persisted)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
