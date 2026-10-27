import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.videos import connect, error, settings

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
MODEL_ID = "fake-detector-v1"


class JobCreate(BaseModel):
    video_id: UUID


class Job(BaseModel):
    job_id: UUID
    video_id: UUID
    status: str
    model_id: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    processed_frames: int
    detection_count: int


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_connection(storage: Path) -> sqlite3.Connection:
    connection = connect(storage)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS analysis_jobs (
            job_id TEXT PRIMARY KEY, video_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
            model_id TEXT NOT NULL, created_at TEXT NOT NULL,
            started_at TEXT, finished_at TEXT, error_message TEXT,
            processed_frames INTEGER NOT NULL DEFAULT 0, detection_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(video_id) REFERENCES videos(video_id)
        )"""
    )
    return connection


@router.post("", response_model=Job, status_code=201)
def create_job(request: JobCreate) -> Job:
    storage, _, _ = settings()
    with closing(job_connection(storage)) as connection:
        if connection.execute("SELECT 1 FROM videos WHERE video_id = ?", (str(request.video_id),)).fetchone() is None:
            raise error(404, "video_not_found", "Video was not found.")
        job_id = str(uuid4())
        with connection:
            connection.execute(
                "INSERT INTO analysis_jobs (job_id, video_id, status, model_id, created_at) VALUES (?, ?, 'queued', ?, ?)",
                (job_id, str(request.video_id), MODEL_ID, now()),
            )
        row = connection.execute("SELECT * FROM analysis_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return Job(**dict(row))


def find_job(storage: Path, job_id: UUID) -> Job:
    with closing(job_connection(storage)) as connection:
        row = connection.execute("SELECT * FROM analysis_jobs WHERE job_id = ?", (str(job_id),)).fetchone()
    if row is None:
        raise error(404, "job_not_found", "Job was not found.")
    return Job(**dict(row))


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: UUID) -> Job:
    storage, _, _ = settings()
    return find_job(storage, job_id)


@router.get("/{job_id}/detections")
def get_detections(job_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)) -> dict:
    storage, _, _ = settings()
    job = find_job(storage, job_id)
    if job.status != "succeeded":
        raise error(409, "results_not_ready", "Results are available only for succeeded jobs.")
    result_path = storage / "results" / f"{job_id}.jsonl"
    items = []
    with result_path.open("r", encoding="utf-8") as result_file:
        for index, line in enumerate(result_file):
            if index >= offset + limit:
                break
            if index >= offset:
                items.append(json.loads(line))
    return {"job_id": str(job_id), "offset": offset, "limit": limit, "total": job.detection_count, "items": items}


def claim_job(storage: Path) -> dict | None:
    with closing(job_connection(storage)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE status = 'queued' ORDER BY created_at, job_id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE analysis_jobs SET status = 'running', started_at = ? WHERE job_id = ? AND status = 'queued'",
                (now(), row["job_id"]),
            )
            connection.commit()
            return dict(row)
        except Exception:
            connection.rollback()
            raise
