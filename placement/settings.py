"""Runtime settings edited from the dashboard Settings page.

Values saved in data/settings.json win; environment variables are the fallback,
then the defaults below. Secrets are never returned to the browser in full.
"""

import json
import os
import threading

from . import config

DEFAULTS = {
    # AI (Groq first, local Ollama as fallback)
    "GROQ_API_KEY": "",
    "GROQ_MODEL": "llama-3.3-70b-versatile",
    "OLLAMA_MODEL": "",
    # Automatic applications
    "AUTO_SUBMIT_ENABLED": "1",
    "AUTO_APPLY_ALLOWED_HOSTS": "*",
    "DAILY_APPLY_LIMIT": "40",
    "APPLY_DELAY_MIN_SECONDS": "45",
    "APPLY_DELAY_MAX_SECONDS": "120",
    "SERVER_HEADLESS": "1",
    "TAILOR_CV": "1",
    "AI_ANSWERS_AUTO_APPROVE": "1",
    # Recruiter emails
    "AUTO_EMAIL_ENABLED": "1",
    "DAILY_EMAIL_LIMIT": "25",
    "EMAIL_GUESSING": "1",
    "SMTP_HOST": "",
    "SMTP_PORT": "465",
    "SMTP_USERNAME": "",
    "SMTP_PASSWORD": "",
    "SMTP_FROM": "",
    "SMTP_FROM_NAME": "Brit Institute Placements",
    "IMAP_HOST": "",
    "IMAP_PORT": "993",
    "IMAP_USERNAME": "",
    "IMAP_PASSWORD": "",
    # Job discovery
    "REED_API_KEY": "",
    "ADZUNA_APP_ID": "",
    "ADZUNA_APP_KEY": "",
    "JOOBLE_API_KEY": "",
    "JOBSPY_RESULTS": "30",
    "DISCOVERY_HOURS_OLD": "72",
}
SECRETS = {"GROQ_API_KEY", "SMTP_PASSWORD", "IMAP_PASSWORD", "REED_API_KEY", "ADZUNA_APP_KEY", "JOOBLE_API_KEY"}
_lock = threading.Lock()


def _path():
    return config.DATA_DIR / "settings.json"


def _load():
    try:
        with open(_path(), encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get(name, default=None):
    stored = _load().get(name)
    if stored not in (None, ""):
        return str(stored)
    env = os.getenv(name)
    if env not in (None, ""):
        return env
    if default is not None:
        return default
    return DEFAULTS.get(name, "")


def enabled(name):
    return get(name).strip().lower() in ("1", "true", "yes", "on")


def number(name, default=0):
    try:
        return int(float(get(name)))
    except (TypeError, ValueError):
        return default


def public():
    """Settings for the dashboard; secrets are masked."""
    result = {}
    for name in DEFAULTS:
        value = get(name)
        if name in SECRETS:
            result[name] = ("••••" + value[-4:]) if value else ""
        else:
            result[name] = value
    result["_configured"] = {name: bool(get(name)) for name in SECRETS}
    return result


def update(values):
    if not isinstance(values, dict):
        raise ValueError("Settings must be an object")
    with _lock:
        current = _load()
        for name, value in values.items():
            if name not in DEFAULTS:
                continue
            value = "" if value is None else str(value).strip()
            # The dashboard shows masked secrets; an unchanged mask must not overwrite the key.
            if name in SECRETS and value.startswith("••••"):
                continue
            if len(value) > 2000:
                raise ValueError(f"{name} is too long")
            current[name] = value
        config.bootstrap_directories()
        target = _path()
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, target)
    return public()
