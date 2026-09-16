import re
import json
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterator
from urllib.parse import urlparse


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class EvaluationRunLockUnavailable(RuntimeError):
    pass


def validate_evaluation_run_id(run_id: str) -> str:
    """Return one safe filesystem segment suitable for an evaluation run."""
    if (
        not isinstance(run_id, str)
        or not _SAFE_RUN_ID.fullmatch(run_id)
        or run_id in {".", ".."}
        or run_id.endswith((".", " "))
    ):
        raise ValueError("run_id must be one safe relative path segment")
    for path_type in (PurePosixPath, PureWindowsPath):
        path = path_type(run_id)
        if path.is_absolute() or path.drive or path.root or len(path.parts) != 1:
            raise ValueError("run_id must be one safe relative path segment")
    return run_id


def resolve_evaluation_run_dir(root: Path, run_id: str) -> Path:
    safe_run_id = validate_evaluation_run_id(run_id)
    resolved_root = Path(root).resolve()
    run_dir = (resolved_root / safe_run_id).resolve()
    if run_dir.parent != resolved_root:
        raise ValueError("run_id resolves outside the evaluation output root")
    return run_dir


class EvaluationArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @classmethod
    def create(cls, *, root: Path, run_id: str, manifest: dict) -> "EvaluationArtifactStore":
        store = cls(resolve_evaluation_run_dir(root, run_id))
        store.run_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            store.run_dir.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise FileExistsError(
                f"evaluation run directory already exists: {store.run_dir}"
            ) from exc
        sanitized = dict(manifest)
        base_url = sanitized.pop("base_url", "")
        if base_url:
            sanitized["base_url_host"] = urlparse(base_url).hostname or ""
        store._write_json(store.run_dir / "manifest.json", sanitized)
        return store

    @classmethod
    def open(cls, *, root: Path, run_id: str) -> "EvaluationArtifactStore":
        run_dir = resolve_evaluation_run_dir(root, run_id)
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"evaluation manifest not found: {manifest_path}")
        return cls(run_dir)

    @contextmanager
    def exclusive_run_lock(self) -> Iterator[None]:
        """Hold a non-blocking process lock for the complete run mutation."""
        lock_path = self.run_dir / ".evaluation.lock"
        handle = lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
            except OSError as exc:
                raise EvaluationRunLockUnavailable(
                    f"evaluation run is already locked: {self.run_dir.name}"
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def read_manifest(self) -> dict:
        return json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, payload: dict) -> None:
        sanitized = dict(payload)
        base_url = sanitized.pop("base_url", "")
        if base_url:
            sanitized["base_url_host"] = urlparse(base_url).hostname or ""
        self._write_json(self.run_dir / "manifest.json", sanitized)

    def attempt_directory(self, case_id: str, run_number: int) -> Path:
        path = self.run_dir / "attempts" / case_id / f"run-{run_number}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_attempt(self, case_id: str, run_number: int, *, normalized: dict) -> None:
        self._write_json(
            self.attempt_directory(case_id, run_number) / "normalized.json",
            normalized,
        )

    def write_error(self, case_id: str, run_number: int, payload: dict) -> None:
        self._write_json(
            self.attempt_directory(case_id, run_number) / "error.json",
            payload,
        )

    def pending_attempts(
        self,
        case_ids: list[str],
        *,
        runs_per_case: int,
    ) -> list[tuple[str, int]]:
        pending: list[tuple[str, int]] = []
        for case_id in case_ids:
            for run_number in range(1, runs_per_case + 1):
                path = (
                    self.run_dir
                    / "attempts"
                    / case_id
                    / f"run-{run_number}"
                    / "normalized.json"
                )
                if not path.exists():
                    pending.append((case_id, run_number))
        return pending

    def load_normalized_attempts(self) -> list[dict]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.run_dir.glob("attempts/*/run-*/normalized.json"))
        ]

    def write_metrics(self, payload: dict) -> None:
        self._write_json(self.run_dir / "metrics.json", payload)

    def write_report(self, content: str) -> None:
        self._write_text(self.run_dir / "report.md", content)

    @classmethod
    def _write_json(cls, path: Path, payload: dict) -> None:
        cls._write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
