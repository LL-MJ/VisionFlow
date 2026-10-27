import argparse
import json
import logging
import os
import subprocess
import threading
import time
from contextlib import closing, contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from app.detector import Detector, FakeDetector, FrameInfo
from app.jobs import claim_job, job_connection, now
from app.videos import settings

logger = logging.getLogger(__name__)
SAMPLE_INTERVAL_MS = 500
PROBE_TIMEOUT_SECONDS = 120


@contextmanager
def worker_lock(storage: Path):
    """Hold an OS lock for the life of the single worker for this storage directory."""
    storage.mkdir(parents=True, exist_ok=True)
    with (storage / "worker.lock").open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another worker is already running for this storage directory.") from exc
        try:
            yield
        finally:
            lock_file.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def recover_interrupted(storage: Path) -> int:
    """Call only while holding worker_lock, so running rows belong to a stopped worker."""
    results = storage / "results"
    with closing(job_connection(storage)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute("SELECT job_id FROM analysis_jobs WHERE status = 'running'").fetchall()
            for row in rows:
                job_id = UUID(row["job_id"])
                (results / f"{job_id}.part").unlink(missing_ok=True)
                (results / f"{job_id}.jsonl").unlink(missing_ok=True)
            connection.execute(
                "UPDATE analysis_jobs SET status='failed', finished_at=?, error_message=? WHERE status='running'",
                (now(), "Worker stopped before completing this job."),
            )
            connection.commit()
            return len(rows)
        except Exception:
            connection.rollback()
            raise


def frame_source(path: Path):
    """Stream frame metadata; timestamps are relative to the container's start time."""
    try:
        origin_output = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
             "-show_entries", "format=start_time", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        origin = Decimal(origin_output)
        if not origin.is_finite():
            raise ValueError("invalid start time")
    except (OSError, subprocess.SubprocessError, InvalidOperation, ValueError) as exc:
        raise RuntimeError("Could not read video start time.") from exc

    command = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
               "-select_streams", "v:0", "-show_frames",
               "-show_entries", "frame=best_effort_timestamp_time,width,height",
               "-of", "compact=p=0:nk=0", str(path)]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError("Could not start ffprobe.") from exc

    timed_out = threading.Event()

    def stop_timed_out_process():
        if process.poll() is None:
            timed_out.set()
            try:
                process.kill()
            except OSError:
                pass

    timer = threading.Timer(PROBE_TIMEOUT_SECONDS, stop_timed_out_process)
    timer.daemon = True
    timer.start()
    previous_ms = -1
    next_sample_ms = 0
    frame_index = 0
    selected = 0
    try:
        assert process.stdout is not None
        for line in process.stdout:
            values = dict(part.split("=", 1) for part in line.strip().split("|") if "=" in part)
            if "width" not in values and "best_effort_timestamp_time" not in values:
                continue  # Ignore optional side-data lines.
            try:
                timestamp = Decimal(values["best_effort_timestamp_time"])
                if not timestamp.is_finite():
                    raise ValueError("invalid timestamp")
                timestamp_ms = round((timestamp - origin) * 1000)
                width, height = int(values["width"]), int(values["height"])
                if width <= 0 or height <= 0:
                    raise ValueError("invalid dimensions")
            except (KeyError, ValueError, InvalidOperation, OverflowError) as exc:
                raise RuntimeError("Video frame timestamp or dimensions are invalid.") from exc
            if timestamp_ms >= 0:
                if timestamp_ms < previous_ms:
                    raise RuntimeError("Video frame timestamps are not monotonic.")
                previous_ms = timestamp_ms
                if timestamp_ms >= next_sample_ms:
                    selected += 1
                    next_sample_ms = timestamp_ms + SAMPLE_INTERVAL_MS
                    yield FrameInfo(frame_index=frame_index, timestamp_ms=timestamp_ms, width=width, height=height)
            frame_index += 1
        return_code = process.wait()
        if timed_out.is_set():
            raise RuntimeError("Video frame inspection timed out.")
        if return_code != 0 or selected == 0:
            raise RuntimeError("Unable to read video frames.")
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


def process_job(storage: Path, job: dict, detector: Detector) -> None:
    job_id, video_id = UUID(job["job_id"]), UUID(job["video_id"])
    results = storage / "results"
    results.mkdir(parents=True, exist_ok=True)
    temporary = results / f"{job_id}.part"
    destination = results / f"{job_id}.jsonl"
    try:
        processed_frames = 0
        detection_count = 0
        with temporary.open("x", encoding="utf-8", newline="\n") as output, closing(
            frame_source(storage / "uploads" / f"{video_id}.mp4")
        ) as frames:
            for frame in frames:
                processed_frames += 1
                for detection in detector.detect(frame):
                    result = {"job_id": str(job_id), "video_id": str(video_id), "model_id": job["model_id"],
                              "timestamp_ms": frame.timestamp_ms, "frame_index": frame.frame_index, **detection}
                    output.write(json.dumps(result, ensure_ascii=False) + "\n")
                    detection_count += 1
        temporary.replace(destination)
        with closing(job_connection(storage)) as connection:
            with connection:
                updated = connection.execute(
                    "UPDATE analysis_jobs SET status='succeeded', finished_at=?, processed_frames=?, detection_count=? "
                    "WHERE job_id=? AND status='running'",
                    (now(), processed_frames, detection_count, str(job_id)),
                ).rowcount
                if updated != 1:
                    raise RuntimeError("Job is no longer running.")
        logger.info("Job %s succeeded: %s frames, %s fake detections", job_id, processed_frames, detection_count)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        message = str(exc) if type(exc) is RuntimeError else "Video processing failed; see worker log."
        with closing(job_connection(storage)) as connection:
            with connection:
                connection.execute(
                    "UPDATE analysis_jobs SET status='failed', finished_at=?, error_message=? WHERE job_id=? AND status='running'",
                    (now(), message[:500], str(job_id)),
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="VisionFlow single worker using fake detections")
    parser.add_argument("--once", action="store_true", help="process at most one queued job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    storage, _, _ = settings()
    with worker_lock(storage):
        recovered = recover_interrupted(storage)
        logger.info("Worker started; marked %s interrupted jobs as failed", recovered)
        detector = FakeDetector()
        while True:
            job = claim_job(storage)
            if job is not None:
                process_job(storage, job, detector)
            if args.once:
                break
            if job is None:
                time.sleep(1)


if __name__ == "__main__":
    main()
