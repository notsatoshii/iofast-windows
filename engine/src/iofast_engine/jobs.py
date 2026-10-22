"""Job scheduling support: track last-run times for weekly jobs.

Stores state in %APPDATA%\iofast\jobs.json. Used by --if-due flag so the
tray app can call jobs at startup and catch up missed weeks.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path


def _jobs_path() -> Path:
    """Get the jobs.json path under APPDATA/iofast/."""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "iofast" / "jobs.json"


def _load_jobs() -> dict:
    """Load jobs.json. Returns empty dict on failure."""
    path = _jobs_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_jobs(jobs: dict) -> None:
    """Save jobs.json atomically."""
    path = _jobs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def is_job_due(job_name: str) -> bool:
    """Check if a weekly job is due (last run > 7 days ago or never run)."""
    jobs = _load_jobs()
    last_run = jobs.get(job_name)
    if not last_run:
        return True
    try:
        last_date = date.fromisoformat(last_run)
    except ValueError:
        return True
    return (date.today() - last_date).days >= 7


def record_job_run(job_name: str) -> None:
    """Record that a job ran today."""
    jobs = _load_jobs()
    jobs[job_name] = date.today().isoformat()
    _save_jobs(jobs)


def get_last_run(job_name: str) -> str | None:
    """Get the last run date for a job, or None."""
    jobs = _load_jobs()
    return jobs.get(job_name)