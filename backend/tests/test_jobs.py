import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.detector import FakeDetector
from app.jobs import claim_job
from app.main import app
from app.worker import frame_source, process_job, worker_lock


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
    path = tmp_path_factory.mktemp("worker-video") / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=5:d=1",
         "-c:v", "mpeg4", "-y", str(path)],
        check=True, capture_output=True, timeout=20,
    )
    return path.read_bytes()


def create_video(client: TestClient, data: bytes) -> str:
    response = client.post("/api/v1/videos", files={"file": ("sample.mp4", data, "video/mp4")})
    assert response.status_code == 201
    return response.json()["video_id"]


def create_job(client: TestClient, video_id: str) -> dict:
    response = client.post("/api/v1/jobs", json={"video_id": video_id})
    assert response.status_code == 201
    return response.json()


def test_end_to_end_separate_worker(storage: Path, video_bytes: bytes) -> None:
    with TestClient(app) as client:
        video_id = create_video(client, video_bytes)
        job = create_job(client, video_id)
        job_id = job["job_id"]
        assert job["status"] == "queued"
        assert client.get(f"/api/v1/jobs/{job_id}/detections").status_code == 409

        worker = subprocess.run([sys.executable, "-m", "app.worker", "--once"],
                                capture_output=True, text=True, env=os.environ.copy(), timeout=30)
        assert worker.returncode == 0, worker.stderr
        finished = client.get(f"/api/v1/jobs/{job_id}").json()
        assert finished["status"] == "succeeded"
        assert finished["started_at"] and finished["finished_at"]
        assert (finished["processed_frames"], finished["detection_count"]) == (2, 2)
        response = client.get(f"/api/v1/jobs/{job_id}/detections", params={"offset": 0, "limit": 1})
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 2 and len(page["items"]) == 1
        detection = page["items"][0]
        assert detection["model_id"] == "fake-detector-v1"
        assert detection["class_name"] == "fake_object"
        assert detection["timestamp_ms"] == 0
        assert detection["bbox_xyxy"] == [0, 0, 64, 48]
        second = client.get(f"/api/v1/jobs/{job_id}/detections", params={"offset": 1, "limit": 1}).json()
        assert second["items"][0]["timestamp_ms"] == 600
        assert second["items"][0]["frame_index"] == 3
        assert client.get(f"/api/v1/jobs/{job_id}/detections", params={"limit": 101}).status_code == 422
        assert claim_job(storage) is None


def test_unknown_video_and_job(storage: Path) -> None:
    client = TestClient(app)
    assert client.post("/api/v1/jobs", json={"video_id": str(uuid4())}).status_code == 404
    assert client.get(f"/api/v1/jobs/{uuid4()}").status_code == 404


def test_simultaneous_claims_do_not_duplicate(storage: Path, video_bytes: bytes) -> None:
    client = TestClient(app)
    job = create_job(client, create_video(client, video_bytes))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_job(storage), range(2)))
    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1 and claimed[0]["job_id"] == job["job_id"]
    assert client.get(f"/api/v1/jobs/{job['job_id']}").json()["status"] == "running"


def test_worker_failure_cleans_partial_output(storage: Path, video_bytes: bytes) -> None:
    class BrokenDetector:
        calls = 0

        def detect(self, _frame):
            self.calls += 1
            if self.calls == 2:
                raise ValueError("simulated model failure")
            return FakeDetector().detect(_frame)

    client = TestClient(app)
    job = create_job(client, create_video(client, video_bytes))
    claimed = claim_job(storage)
    assert claimed is not None
    process_job(storage, claimed, BrokenDetector())
    result = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert result["status"] == "failed" and result["error_message"]
    assert result["finished_at"]
    assert not (storage / "results" / f"{job['job_id']}.part").exists()
    assert not (storage / "results" / f"{job['job_id']}.jsonl").exists()


def test_empty_detections_are_successful(storage: Path, video_bytes: bytes) -> None:
    class EmptyDetector:
        def detect(self, _frame):
            return []

    client = TestClient(app)
    job = create_job(client, create_video(client, video_bytes))
    claimed = claim_job(storage)
    assert claimed is not None
    process_job(storage, claimed, EmptyDetector())
    result = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert result["status"] == "succeeded" and result["detection_count"] == 0
    assert client.get(f"/api/v1/jobs/{job['job_id']}/detections").json()["items"] == []


def test_missing_video_marks_job_failed(storage: Path, video_bytes: bytes) -> None:
    client = TestClient(app)
    video_id = create_video(client, video_bytes)
    job = create_job(client, video_id)
    (storage / "uploads" / f"{video_id}.mp4").unlink()
    claimed = claim_job(storage)
    assert claimed is not None
    process_job(storage, claimed, FakeDetector())
    result = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert result["status"] == "failed" and result["error_message"]
    assert client.get(f"/api/v1/jobs/{job['job_id']}/detections").status_code == 409


def test_worker_lock_and_interrupted_recovery(storage: Path, video_bytes: bytes) -> None:
    client = TestClient(app)
    job = create_job(client, create_video(client, video_bytes))
    assert claim_job(storage) is not None
    results = storage / "results"
    results.mkdir()
    (results / f"{job['job_id']}.part").write_text("partial", encoding="utf-8")
    (results / f"{job['job_id']}.jsonl").write_text("orphan", encoding="utf-8")
    with worker_lock(storage):
        blocked = subprocess.run([sys.executable, "-m", "app.worker", "--once"],
                                 capture_output=True, text=True, env=os.environ.copy(), timeout=30)
        assert blocked.returncode != 0
        assert "Another worker is already running" in blocked.stderr
        assert client.get(f"/api/v1/jobs/{job['job_id']}").json()["status"] == "running"
    restarted = subprocess.run([sys.executable, "-m", "app.worker", "--once"],
                               capture_output=True, text=True, env=os.environ.copy(), timeout=30)
    assert restarted.returncode == 0, restarted.stderr
    result = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert result["status"] == "failed" and "stopped" in result["error_message"]
    assert list(results.iterdir()) == []


def test_nonzero_video_start_uses_relative_source_time(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required for video integration tests")
    path = tmp_path / "offset.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=5:d=1",
         "-c:v", "mpeg4", "-output_ts_offset", "1", "-y", str(path)],
        check=True, capture_output=True, timeout=20,
    )
    frames = list(frame_source(path))
    assert [frame.timestamp_ms for frame in frames] == [0, 600]
    assert [frame.frame_index for frame in frames] == [0, 3]
