"""Configuration intentionally defaults to local-only operation."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path=ROOT / ".env"):
    """KEY=VALUE lines from .env; variables already set in the environment win."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("'\""))


load_env_file()
DATA_DIR = Path(os.getenv("PLACEMENT_DATA_DIR", str(ROOT / "data"))).resolve()
DB_PATH = DATA_DIR / "placement.sqlite3"
CV_DIR = DATA_DIR / "cvs"
HOST = os.getenv("PLACEMENT_HOST", "127.0.0.1")
PORT = int(os.getenv("PLACEMENT_PORT", "8770"))
USER_AGENT = "BritInstitutePlacementBot/1.0 (+https://britinstitute.uk)"
SOURCE_TIMEOUT = int(os.getenv("SOURCE_TIMEOUT", "15"))


def bootstrap_directories():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CV_DIR.mkdir(parents=True, exist_ok=True)
