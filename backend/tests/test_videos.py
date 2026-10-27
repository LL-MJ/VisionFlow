import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import videos


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "storage"
    monkeypatch.setenv("VISIONFLOW_STORAGE_DIR", str(path))
    monkeypatch.delenv("VISIONFLOW_MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("VISIONFLOW_MAX_DURATION_SECONDS", raising=False)
    return path


@pytest.fixture(scope="session")
def video_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required for video integration tests")
    path = tmp_path_factory.mktemp("synthetic-video") / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=5:d=1",
         "-c:v", "mpeg4", "-y", str(path)],
        check=True, capture_output=True, timeout=20,
    )
    return path.read_bytes()


def upload(client: TestClient, data: bytes, filename: str = "sample.mp4"):
    return client.post("/api/v1/videos", files={"file": (filename, data, "video/mp4")})


def assert_no_uploads(storage: Path) -> None:
    uploads = storage / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_upload_and_lookup_persists_metadata(storage: Path, video_bytes: bytes) -> None:
    with TestClient(app) as client:
        response = upload(client, video_bytes, "../sample.mp4")
        assert response.status_code == 201
        video = response.json()
        assert video["original_filename"] == "sample.mp4"
        assert video["duration_ms"] == 1000
        assert (video["width"], video["height"], video["fps"]) == (64, 48, 5.0)
        assert "stored_path" not in video
        assert client.get(f"/api/v1/videos/{video['video_id']}").json() == video
    with TestClient(app) as client:
        assert client.get(f"/api/v1/videos/{video['video_id']}").json() == video
    assert (storage / "uploads" / f"{video['video_id']}.mp4").read_bytes() == video_bytes


@pytest.mark.parametrize("filename", ["sample.avi", "sample.mp4.exe", ""])
def test_rejects_wrong_extension(storage: Path, video_bytes: bytes, filename: str) -> None:
    response = upload(TestClient(app), video_bytes, filename)
    # An empty filename is treated as a non-file form field by the multipart parser.
    assert response.status_code == (422 if filename == "" else 415)
    assert response.json()["detail"]["code"] == ("invalid_request" if filename == "" else "invalid_file_type")
    assert_no_uploads(storage)


def test_rejects_invalid_and_corrupt_mp4(storage: Path) -> None:
    client = TestClient(app)
    for content in [b"not an mp4", b"\x00\x00\x00\x18ftypisom" + b"broken"]:
        response = upload(client, content)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_video"
    assert_no_uploads(storage)


def test_rejects_size_limit(storage: Path, video_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISIONFLOW_MAX_UPLOAD_BYTES", str(len(video_bytes) - 1))
    response = upload(TestClient(app), video_bytes)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"
    assert_no_uploads(storage)


def test_accepts_exact_size_and_duration_limits(storage: Path, video_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISIONFLOW_MAX_UPLOAD_BYTES", str(len(video_bytes)))
    monkeypatch.setenv("VISIONFLOW_MAX_DURATION_SECONDS", "1")
    assert upload(TestClient(app), video_bytes).status_code == 201


def test_rejects_duration_limit(storage: Path, video_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISIONFLOW_MAX_DURATION_SECONDS", "0.5")
    response = upload(TestClient(app), video_bytes)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "video_too_long"
    assert_no_uploads(storage)


def test_unknown_video_returns_404(storage: Path) -> None:
    response = TestClient(app).get("/api/v1/videos/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "video_not_found"


def test_missing_ffprobe_returns_503(storage: Path, video_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    response = upload(TestClient(app), video_bytes)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ffprobe_unavailable"
    assert_no_uploads(storage)


def test_database_failure_removes_video(storage: Path, video_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_connect(_storage: Path) -> None:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(videos, "connect", fail_connect)
    response = upload(TestClient(app, raise_server_exceptions=False), video_bytes)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "internal_error"
    assert_no_uploads(storage)
