"""Configuration intentionally defaults to local-only operation."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PLACEMENT_DATA_DIR", str(ROOT / "data"))).resolve()
DB_PATH = DATA_DIR / "placement.sqlite3"
CV_DIR = DATA_DIR / "cvs"
HOST = os.getenv("PLACEMENT_HOST", "127.0.0.1")
PORT = int(os.getenv("PLACEMENT_PORT", "8765"))
USER_AGENT = "BritInstitutePlacementBot/1.0 (+https://britinstitute.uk)"
SOURCE_TIMEOUT = int(os.getenv("SOURCE_TIMEOUT", "15"))


def bootstrap_directories():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CV_DIR.mkdir(parents=True, exist_ok=True)
