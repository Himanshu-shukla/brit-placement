"""SQLite storage with constraints to avoid duplicate submissions."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection():
    config.bootstrap_directories()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS learners (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 phone TEXT DEFAULT '', location TEXT DEFAULT '', skills TEXT DEFAULT '',
 summary TEXT DEFAULT '', projects TEXT DEFAULT '',
 experience_years REAL DEFAULT 0, right_to_work TEXT DEFAULT 'unknown',
 sponsorship INTEGER DEFAULT 0, cv_path TEXT DEFAULT '', active INTEGER DEFAULT 1,
 permission_to_apply INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, domain TEXT DEFAULT '',
 careers_url TEXT DEFAULT '', ats TEXT DEFAULT 'manual', ats_slug TEXT DEFAULT '',
 last_synced_at TEXT DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(ats, ats_slug)
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id),
 source TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
 location TEXT DEFAULT '', description TEXT DEFAULT '', url TEXT NOT NULL,
 posted_at TEXT DEFAULT '', found_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 active INTEGER DEFAULT 1, UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS contacts (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 email TEXT NOT NULL, source_url TEXT NOT NULL, kind TEXT DEFAULT 'published',
 discovered_at TEXT NOT NULL, UNIQUE(company_id, email)
);
CREATE TABLE IF NOT EXISTS contact_forms (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 url TEXT NOT NULL, discovered_at TEXT NOT NULL, UNIQUE(company_id, url)
);
CREATE TABLE IF NOT EXISTS matches (
 id INTEGER PRIMARY KEY, learner_id INTEGER NOT NULL REFERENCES learners(id),
 job_id INTEGER NOT NULL REFERENCES jobs(id), score INTEGER NOT NULL,
 decision TEXT NOT NULL, reasons TEXT NOT NULL, matched_at TEXT NOT NULL,
 UNIQUE(learner_id,job_id)
);
CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY, learner_id INTEGER NOT NULL REFERENCES learners(id),
 job_id INTEGER NOT NULL REFERENCES jobs(id), status TEXT NOT NULL DEFAULT 'queued',
 method TEXT DEFAULT 'browser_assisted', confirmation TEXT DEFAULT '',
 notes TEXT DEFAULT '', updated_at TEXT NOT NULL,
 UNIQUE(learner_id,job_id)
);
CREATE TABLE IF NOT EXISTS outreach (
 id INTEGER PRIMARY KEY, learner_id INTEGER NOT NULL REFERENCES learners(id),
 job_id INTEGER NOT NULL REFERENCES jobs(id), contact_id INTEGER NOT NULL REFERENCES contacts(id),
 subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT DEFAULT 'draft',
 sent_at TEXT DEFAULT '', response TEXT DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(learner_id,job_id,contact_id)
);
CREATE TABLE IF NOT EXISTS suppressions (
 email TEXT PRIMARY KEY, reason TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sponsors (
 name TEXT PRIMARY KEY, normalized_name TEXT NOT NULL, route TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, detail TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_matches_learner ON matches(learner_id);
CREATE TABLE IF NOT EXISTS loops (
 id INTEGER PRIMARY KEY, learner_id INTEGER NOT NULL REFERENCES learners(id),
 name TEXT NOT NULL, titles TEXT NOT NULL, location TEXT DEFAULT 'United Kingdom',
 radius_km INTEGER DEFAULT 30, remote_only INTEGER DEFAULT 0, hybrid_only INTEGER DEFAULT 0,
 boards TEXT DEFAULT '[]', experience TEXT DEFAULT 'junior', job_type TEXT DEFAULT 'full_time',
 min_salary INTEGER DEFAULT 0, max_salary INTEGER DEFAULT 0, industries TEXT DEFAULT '',
 excluded_companies TEXT DEFAULT '', min_score INTEGER DEFAULT 60,
 mode TEXT NOT NULL DEFAULT 'review', cv_version_id INTEGER,
 email_template TEXT DEFAULT '', frequency_minutes INTEGER DEFAULT 120,
 active INTEGER DEFAULT 1, last_run_at TEXT DEFAULT '', next_run_at TEXT DEFAULT '',
 last_error TEXT DEFAULT '', run_count INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loop_jobs (
 id INTEGER PRIMARY KEY, loop_id INTEGER NOT NULL REFERENCES loops(id),
 job_id INTEGER NOT NULL REFERENCES jobs(id), score INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'matched', reason TEXT DEFAULT '',
 first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(loop_id,job_id)
);
CREATE INDEX IF NOT EXISTS idx_loop_jobs_loop ON loop_jobs(loop_id,status);
CREATE TABLE IF NOT EXISTS cv_versions (
 id INTEGER PRIMARY KEY, learner_id INTEGER NOT NULL REFERENCES learners(id),
 label TEXT NOT NULL, filename TEXT NOT NULL, mime_type TEXT NOT NULL,
 path TEXT NOT NULL, is_default INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS question_answers (
 id INTEGER PRIMARY KEY, learner_id INTEGER NOT NULL REFERENCES learners(id),
 question TEXT NOT NULL, normalized_question TEXT NOT NULL,
 answer TEXT NOT NULL, approved INTEGER DEFAULT 0, source TEXT DEFAULT 'manual',
 occurrence_count INTEGER DEFAULT 0, updated_at TEXT NOT NULL,
 UNIQUE(learner_id,normalized_question)
);
CREATE TABLE IF NOT EXISTS application_attempts (
 id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id),
 loop_id INTEGER REFERENCES loops(id), engine TEXT NOT NULL, status TEXT NOT NULL,
 steps INTEGER DEFAULT 0, reason TEXT DEFAULT '', evidence TEXT DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_attempts_app ON application_attempts(application_id);
CREATE TABLE IF NOT EXISTS tailored_cvs (
 id INTEGER PRIMARY KEY, learner_id INTEGER NOT NULL REFERENCES learners(id),
 job_id INTEGER NOT NULL REFERENCES jobs(id), cv_version_id INTEGER NOT NULL REFERENCES cv_versions(id),
 keywords TEXT DEFAULT '', created_at TEXT NOT NULL, UNIQUE(learner_id,job_id)
);
"""

# Columns added after the first release; applied idempotently on start-up.
MIGRATIONS = {
    "learners": ["linkedin_url TEXT DEFAULT ''", "portfolio_url TEXT DEFAULT ''", "postcode TEXT DEFAULT ''",
                 "address TEXT DEFAULT ''", "salary_expectation TEXT DEFAULT ''", "notice_period TEXT DEFAULT ''",
                 "education TEXT DEFAULT ''", "work_history TEXT DEFAULT ''", "extra_facts TEXT DEFAULT ''"],
    "jobs": ["apply_url TEXT DEFAULT ''", "company_url TEXT DEFAULT ''", "emails TEXT DEFAULT ''",
             "easy_apply INTEGER DEFAULT 0", "poster_name TEXT DEFAULT ''", "salary TEXT DEFAULT ''"],
    "applications": ["channel TEXT DEFAULT ''", "apply_url TEXT DEFAULT ''", "cv_version_id INTEGER",
                     "attempt_count INTEGER DEFAULT 0", "claimed_at TEXT DEFAULT ''",
                     "submitted_at TEXT DEFAULT ''", "loop_id INTEGER"],
    "contacts": ["name TEXT DEFAULT ''", "confidence TEXT DEFAULT 'published'"],
    "outreach": ["auto INTEGER DEFAULT 0", "cv_version_id INTEGER"],
    "loops": ["auto_email INTEGER DEFAULT 1", "tailor_cv INTEGER DEFAULT 1"],
}

LEARNER_EXTRAS = ("linkedin_url", "portfolio_url", "postcode", "address", "salary_expectation", "notice_period",
                  "education", "work_history", "extra_facts")
LEARNER_FIELDS = ("name", "email", "phone", "location", "skills", "summary", "projects", "experience_years",
                  "right_to_work", "sponsorship", "permission_to_apply", "active") + LEARNER_EXTRAS


def initialize():
    with connection() as conn:
        conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            existing = {item[1] for item in conn.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column.split()[0] not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_applications_queue ON applications(status,channel)")


def rows(sql, params=()):
    with connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def row(sql, params=()):
    found = rows(sql, params)
    return found[0] if found else None


def run(sql, params=()):
    with connection() as conn:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid


def log_event(kind, detail):
    run("INSERT INTO events(kind,detail,created_at) VALUES (?,?,?)",
        (kind, json.dumps(detail, default=str), utc_now()))


def upsert_company(name, domain="", careers_url="", ats="manual", ats_slug=""):
    name = name.strip()
    if not name:
        raise ValueError("Company name is required")
    ats = ats.strip().lower() or "manual"
    slug = ats_slug.strip()
    # Manual companies use their domain as the stable key if available.
    if ats == "manual":
        slug = slug or domain.strip().lower() or name.lower()
    with connection() as conn:
        conn.execute("""INSERT INTO companies
            (name,domain,careers_url,ats,ats_slug,created_at)
            VALUES (?,?,?,?,?,?) ON CONFLICT(ats,ats_slug) DO UPDATE SET
            name=excluded.name,
            domain=COALESCE(NULLIF(excluded.domain,''),companies.domain),
            careers_url=COALESCE(NULLIF(excluded.careers_url,''),companies.careers_url)""",
            (name, domain, careers_url, ats, slug, utc_now()))
        return conn.execute("SELECT id FROM companies WHERE ats=? AND ats_slug=?", (ats,slug)).fetchone()[0]


JOB_EXTRAS = ("apply_url", "company_url", "emails", "easy_apply", "poster_name", "salary")


def upsert_job(company_id, source, source_id, title, url, description="", location="", posted_at="", **extra):
    if not title or not url or not source_id:
        raise ValueError("A job requires title, URL, and source ID")
    now = utc_now()
    values = {k: ("" if extra.get(k) is None else extra.get(k)) for k in JOB_EXTRAS}
    values["easy_apply"] = int(bool(values["easy_apply"]))
    with connection() as conn:
        conn.execute("""INSERT INTO jobs
          (company_id,source,source_id,title,location,description,url,posted_at,found_at,last_seen_at,
           apply_url,company_url,emails,easy_apply,poster_name,salary)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(source,source_id) DO UPDATE SET
          title=excluded.title, location=COALESCE(NULLIF(excluded.location,''),jobs.location),
          description=COALESCE(NULLIF(excluded.description,''),jobs.description), url=excluded.url,
          apply_url=COALESCE(NULLIF(excluded.apply_url,''),jobs.apply_url),
          company_url=COALESCE(NULLIF(excluded.company_url,''),jobs.company_url),
          emails=COALESCE(NULLIF(excluded.emails,''),jobs.emails),
          easy_apply=MAX(excluded.easy_apply,jobs.easy_apply),
          poster_name=COALESCE(NULLIF(excluded.poster_name,''),jobs.poster_name),
          salary=COALESCE(NULLIF(excluded.salary,''),jobs.salary),
          last_seen_at=excluded.last_seen_at, active=1""",
          (company_id,source,str(source_id),title,location,description,url,posted_at,now,now,
           str(values["apply_url"]),str(values["company_url"]),str(values["emails"]),values["easy_apply"],
           str(values["poster_name"]),str(values["salary"])))
        return conn.execute("SELECT id FROM jobs WHERE source=? AND source_id=?",(source,str(source_id))).fetchone()[0]


def add_learner(data):
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    if not name or "@" not in email:
        raise ValueError("Name and valid email are required")
    skills = data.get("skills", "")
    if isinstance(skills, list):
        skills = ", ".join(map(str, skills))
    with connection() as conn:
        cursor = conn.execute(f"""INSERT INTO learners
         (name,email,phone,location,skills,summary,projects,experience_years,
          right_to_work,sponsorship,permission_to_apply,created_at,{",".join(LEARNER_EXTRAS)})
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?{",?" * len(LEARNER_EXTRAS)})""",
         (name,email,str(data.get("phone","")),str(data.get("location","")),skills,
          str(data.get("summary","")),str(data.get("projects","")),
          float(data.get("experience_years") or 0),str(data.get("right_to_work","unknown")),
          int(bool(data.get("sponsorship",False))),
          int(bool(data.get("permission_to_apply",False))),utc_now(),
          *[str(data.get(field) or "")[:8000] for field in LEARNER_EXTRAS]))
        return cursor.lastrowid


def update_learner(learner_id, data):
    if not row("SELECT id FROM learners WHERE id=?", (int(learner_id),)):
        raise ValueError("Learner not found")
    changes = {}
    for field in LEARNER_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field in ("sponsorship", "permission_to_apply", "active"):
            value = int(value in (True, 1, "1", "true", "on", "yes"))
        elif field == "experience_years":
            value = float(value or 0)
        elif field == "skills" and isinstance(value, list):
            value = ", ".join(map(str, value))
        elif field == "email":
            value = str(value).strip().lower()
            if "@" not in value:
                raise ValueError("Valid email required")
        else:
            value = str(value or "")[:8000]
        changes[field] = value
    if changes:
        assignments = ",".join(f"{field}=?" for field in changes)
        run(f"UPDATE learners SET {assignments} WHERE id=?", (*changes.values(), int(learner_id)))
    return row("SELECT * FROM learners WHERE id=?", (int(learner_id),))


def summary():
    with connection() as conn:
        result = {}
        for table in ("learners","companies","jobs","contacts","matches","applications","outreach","loops","loop_jobs","cv_versions","question_answers","application_attempts"):
            result[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        result["confirmed_applications"] = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status='confirmed'").fetchone()[0]
        result["interviews"] = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status='interview'").fetchone()[0]
        result["offers"] = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status='offer'").fetchone()[0]
        result["active_loops"] = conn.execute("SELECT COUNT(*) FROM loops WHERE active=1").fetchone()[0]
        result["emails_sent"] = conn.execute("SELECT COUNT(*) FROM outreach WHERE status IN ('sent','replied')").fetchone()[0]
        result["pending_applications"] = conn.execute("SELECT COUNT(*) FROM applications WHERE status IN ('queued','prepared','applying')").fetchone()[0]
        result["needs_review"] = conn.execute("SELECT COUNT(*) FROM applications WHERE status='needs_review'").fetchone()[0]
        result["applied_today"] = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status='confirmed' AND submitted_at>=?", (utc_now()[:10],)).fetchone()[0]
        return result
