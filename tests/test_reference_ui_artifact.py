from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "prototypes" / "interview-agent-single-file.html"
EXPECTED_SHA256 = "A4549DD6D1B0F37C4207338E1ABC33D00CD44453A7643FF2DF81F25F3D35E283"


def test_reference_ui_artifact_is_frozen():
    assert REFERENCE.exists()
    normalized = REFERENCE.read_bytes().replace(b"\r\n", b"\n")
    assert sha256(normalized).hexdigest().upper() == EXPECTED_SHA256


def test_reference_ui_is_not_served_from_app_root():
    assert not (ROOT / "app" / "interview-agent-single-file.html").exists()
