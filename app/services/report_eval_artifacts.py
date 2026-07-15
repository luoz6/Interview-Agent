import json
from pathlib import Path
from urllib.parse import urlparse


class EvaluationArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @classmethod
    def create(cls, *, root: Path, run_id: str, manifest: dict) -> "EvaluationArtifactStore":
        store = cls(root / run_id)
        store.run_dir.mkdir(parents=True, exist_ok=True)
        sanitized = dict(manifest)
        base_url = sanitized.pop("base_url", "")
        if base_url:
            sanitized["base_url_host"] = urlparse(base_url).hostname or ""
        store._write_json(store.run_dir / "manifest.json", sanitized)
        return store

    @classmethod
    def open(cls, run_dir: Path) -> "EvaluationArtifactStore":
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"evaluation manifest not found: {manifest_path}")
        return cls(run_dir)

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
