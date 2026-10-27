import json
import math
import os
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/v1/videos", tags=["videos"])
DEFAULT_STORAGE = Path(__file__).resolve().parents[2] / "data"
CHUNK_SIZE = 1024 * 1024


class VideoMetadata(BaseModel):
    video_id: UUID
    original_filename: str
    duration_ms: int
    width: int
    height: int
    fps: float
    created_at: datetime


def error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def settings() -> tuple[Path, int, float]:
    try:
        max_bytes = int(os.getenv("VISIONFLOW_MAX_UPLOAD_BYTES", "104857600"))
        max_seconds = float(os.getenv("VISIONFLOW_MAX_DURATION_SECONDS", "300"))
        if max_bytes <= 0 or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError
    except ValueError as exc:
        raise error(500, "invalid_config", "Upload limits must be positive numbers.") from exc
    storage = Path(os.getenv("VISIONFLOW_STORAGE_DIR", str(DEFAULT_STORAGE))).resolve()
    return storage, max_bytes, max_seconds


def connect(storage: Path) -> sqlite3.Connection:
    storage.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(storage / "visionflow.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY, original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL, duration_ms INTEGER NOT NULL,
            width INTEGER NOT NULL, height INTEGER NOT NULL, fps REAL NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    return connection


def probe_video(path: Path) -> tuple[float, int, int, float]:
    # ISO BMFF brands accepted for common MP4 files; reject renamed arbitrary files.
    with path.open("rb") as source:
        header = source.read(12)
    brands = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"MSNV", b"dash"}
    if len(header) != 12 or header[4:8] != b"ftyp" or header[8:12] not in brands:
        raise error(422, "invalid_video", "The file is not a supported MP4 container.")

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", "mov", "-select_streams", "v:0",
             "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15, check=True,
        )
    except FileNotFoundError as exc:
        raise error(503, "ffprobe_unavailable", "ffprobe is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise error(422, "invalid_video", "Video metadata inspection timed out.") from exc
    except subprocess.CalledProcessError as exc:
        raise error(422, "invalid_video", "The MP4 file could not be inspected.") from exc

    try:
        info = json.loads(result.stdout)
        stream = next(item for item in info["streams"] if item["codec_type"] == "video")
        seconds = float(info["format"]["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        fps = float(Fraction(stream["avg_frame_rate"]))
        if not math.isfinite(seconds) or seconds <= 0 or width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError, StopIteration) as exc:
        raise error(422, "invalid_video", "Video metadata is missing or invalid.") from exc
    return seconds, width, height, fps


@router.post("", response_model=VideoMetadata, status_code=201)
async def upload_video(file: UploadFile = File(...)) -> VideoMetadata:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename.lower().endswith(".mp4") or len(filename) > 255:
        raise error(415, "invalid_file_type", "Upload a file with an .mp4 extension.")

    storage, max_bytes, max_seconds = settings()
    uploads = storage / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    video_id = uuid4()
    temporary = uploads / f"{video_id}.part"
    destination = uploads / f"{video_id}.mp4"
    saved = False
    temporary_created = False
    moved = False
    try:
        with temporary.open("xb") as output:
            temporary_created = True
            total = 0
            while chunk := await file.read(CHUNK_SIZE):
                total += len(chunk)
                if total > max_bytes:
                    raise error(413, "file_too_large", "File exceeds the configured size limit.")
                output.write(chunk)
        seconds, width, height, fps = await run_in_threadpool(probe_video, temporary)
        if seconds > max_seconds:
            raise error(422, "video_too_long", "Video exceeds the configured duration limit.")
        metadata = VideoMetadata(
            video_id=video_id, original_filename=filename,
            duration_ms=round(seconds * 1000), width=width, height=height,
            fps=fps, created_at=datetime.now(timezone.utc),
        )
        if destination.exists():
            raise error(500, "storage_conflict", "Unable to allocate a video file.")
        temporary.replace(destination)
        moved = True
        with closing(connect(storage)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(video_id), filename, destination.name, metadata.duration_ms,
                     width, height, fps, metadata.created_at.isoformat()),
                )
        saved = True
        return metadata
    finally:
        await file.close()
        if temporary_created:
            temporary.unlink(missing_ok=True)
        if moved and not saved:
            destination.unlink(missing_ok=True)


@router.get("/{video_id}", response_model=VideoMetadata)
def get_video(video_id: UUID) -> VideoMetadata:
    storage, _, _ = settings()
    with closing(connect(storage)) as connection:
        row = connection.execute("SELECT * FROM videos WHERE video_id = ?", (str(video_id),)).fetchone()
    if row is None:
        raise error(404, "video_not_found", "Video was not found.")
    return VideoMetadata(**dict(row))
