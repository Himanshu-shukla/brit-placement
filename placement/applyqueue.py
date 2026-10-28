"""Shared application queue used by the Chrome extension and the server browser.

queued -> applying (claimed) -> confirmed | needs_review | queued (rerouted / retried)
"""

import base64
import random
from datetime import datetime, timedelta, timezone

from . import channels, cv_files, db, questions, settings, tailor

STALE_MINUTES = 20
MAX_ATTEMPTS = 3


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def applied_today(learner_id):
    return db.row("""SELECT COUNT(*) AS n FROM applications WHERE learner_id=? AND
        (status='applying' OR (status='confirmed' AND submitted_at>=?))""", (int(learner_id), _today()))["n"]


def release_stale():
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES)).isoformat(timespec="seconds")
    with db.connection() as conn:
        conn.execute("""UPDATE applications SET status=CASE WHEN attempt_count+1>=? THEN 'needs_review' ELSE 'queued' END,
            attempt_count=attempt_count+1, notes='Previous attempt timed out', updated_at=?
            WHERE status='applying' AND claimed_at<>'' AND claimed_at<?""", (MAX_ATTEMPTS, db.utc_now(), cutoff))


def enqueue(conn, learner_id, job, method="loop_auto", loop_id=None):
    """Insert a queued application inside an open transaction; returns True if new."""
    url = channels.target(job)
    cursor = conn.execute("""INSERT INTO applications(learner_id,job_id,method,channel,apply_url,loop_id,updated_at)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(learner_id,job_id) DO NOTHING""",
        (int(learner_id), job["id"], method, channels.channel_for(url), url, loop_id, db.utc_now()))
    return cursor.rowcount > 0


def claim(learner_id=None, channel_list=("extension", "server"), application_id=None):
    """Atomically claim the next queued application, honouring the daily limit."""
    release_stale()
    limit = settings.number("DAILY_APPLY_LIMIT", 40)
    with db.connection() as conn:
        if application_id:
            rows = conn.execute("SELECT * FROM applications WHERE id=? AND status IN ('queued','prepared','needs_review')",
                                (int(application_id),)).fetchall()
        else:
            marks = ",".join("?" * len(channel_list))
            sql = f"""SELECT a.* FROM applications a JOIN learners l ON l.id=a.learner_id
                WHERE a.status='queued' AND a.channel IN ({marks}) AND l.permission_to_apply=1 AND l.active=1"""
            params = list(channel_list)
            if learner_id:
                sql += " AND a.learner_id=?"
                params.append(int(learner_id))
            # Board applications (extension) first, then oldest.
            sql += " ORDER BY CASE a.channel WHEN 'extension' THEN 0 ELSE 1 END, a.id LIMIT 50"
            rows = conn.execute(sql, params).fetchall()
        for item in rows:
            used = conn.execute("""SELECT COUNT(*) FROM applications WHERE learner_id=? AND
                (status='applying' OR (status='confirmed' AND submitted_at>=?))""", (item["learner_id"], _today())).fetchone()[0]
            if not application_id and used >= limit:
                continue
            changed = conn.execute("""UPDATE applications SET status='applying',claimed_at=?,updated_at=?
                WHERE id=? AND status=?""", (db.utc_now(), db.utc_now(), item["id"], item["status"])).rowcount
            if changed:
                return dict(item)
    return None


def payload(application, include_cv=True):
    """Everything an apply engine needs: profile, job, approved answers, CV file."""
    learner = db.row("SELECT * FROM learners WHERE id=?", (application["learner_id"],))
    job = db.row("""SELECT jobs.*,companies.name AS company_name FROM jobs LEFT JOIN companies
        ON companies.id=jobs.company_id WHERE jobs.id=?""", (application["job_id"],))
    loop = db.row("SELECT * FROM loops WHERE id=?", (application["loop_id"],)) if application.get("loop_id") else None
    parts = learner["name"].split()
    profile = {k: learner.get(k) or "" for k in ("name", "email", "phone", "location", "postcode", "address",
                                                  "linkedin_url", "portfolio_url", "salary_expectation", "notice_period")}
    profile.update(first_name=parts[0] if parts else "", last_name=" ".join(parts[1:]),
                   city=(learner.get("location") or "").split(",")[0].strip(), learner_id=learner["id"])
    answers = {row["normalized_question"]: row["answer"] for row in db.rows(
        "SELECT normalized_question,answer FROM question_answers WHERE learner_id=? AND approved=1", (learner["id"],))}
    result = {
        "application": {"id": application["id"], "url": application.get("apply_url") or channels.target(job),
                        "channel": application.get("channel"), "loop_id": application.get("loop_id")},
        "job": {"id": job["id"], "title": job["title"], "company": job.get("company_name") or "",
                "location": job.get("location") or "", "description": str(job.get("description") or "")[:4000],
                "url": job["url"]},
        "profile": profile, "answers": answers,
        "delay": [settings.number("APPLY_DELAY_MIN_SECONDS", 45), settings.number("APPLY_DELAY_MAX_SECONDS", 120)],
    }
    if include_cv:
        version = tailor.cv_for_application(application, loop)
        info, binary = cv_files.read_variant(version["id"])
        db.run("UPDATE applications SET cv_version_id=? WHERE id=?", (info["id"], application["id"]))
        result["cv"] = {"id": info["id"], "filename": info["filename"], "mime_type": info["mime_type"],
                        "content_base64": base64.b64encode(binary).decode()}
    return result


def next_for_extension(learner_id, include_server=True):
    channel_list = ("extension", "server") if include_server else ("extension",)
    item = claim(learner_id, channel_list)
    if not item:
        return {"application": None, "applied_today": applied_today(learner_id),
                "limit": settings.number("DAILY_APPLY_LIMIT", 40)}
    try:
        return payload(item)
    except Exception as exc:
        record(item["id"], {"status": "error", "reason": f"Could not prepare CV/profile: {exc}"}, engine="system")
        raise


def manual(learner_id, job_data):
    """'Apply to this job now' from the extension popup on any job page."""
    from . import sources
    learner = db.row("SELECT * FROM learners WHERE id=?", (int(learner_id),))
    if not learner or not learner["permission_to_apply"]:
        raise ValueError("Learner consent is required before applying")
    url = str(job_data.get("url") or "")
    if not url.startswith("https://"):
        raise ValueError("Open an HTTPS job page")
    job_id = sources.save_job({"source": channels.board_for(url) or "web", "source_id": url.split("#")[0][:400],
                               "title": str(job_data.get("title") or "Job application")[:250], "url": url,
                               "description": str(job_data.get("description") or "")[:20000],
                               "location": str(job_data.get("location") or "")[:200],
                               "company_name": str(job_data.get("company") or channels.host_of(url))[:200],
                               "easy_apply": True})
    with db.connection() as conn:
        conn.execute("""INSERT INTO applications(learner_id,job_id,method,channel,apply_url,updated_at)
            VALUES(?,?,'extension_manual','extension',?,?) ON CONFLICT(learner_id,job_id) DO UPDATE SET
            status=CASE WHEN applications.status IN ('confirmed','interview','offer','hired') THEN applications.status ELSE 'queued' END,
            apply_url=excluded.apply_url""", (learner["id"], job_id, url, db.utc_now()))
        app_row = conn.execute("SELECT * FROM applications WHERE learner_id=? AND job_id=?", (learner["id"], job_id)).fetchone()
    if app_row["status"] in ("confirmed", "interview", "offer", "hired"):
        raise ValueError("Already applied to this job")
    item = claim(application_id=app_row["id"])
    if not item:
        raise ValueError("Application is already being processed")
    return payload(item)


def record(application_id, data, engine="chrome_extension"):
    application = db.row("SELECT * FROM applications WHERE id=?", (int(application_id),))
    if not application:
        raise ValueError("Application not found")
    status = str(data.get("status") or "error")
    reason = str(data.get("reason") or "")[:900]
    evidence = str(data.get("evidence") or "")[:900]
    unknown = data.get("unknown") or []
    if unknown:
        reason = (reason + " | Unanswered: " + "; ".join(map(str, unknown))[:400]).strip(" |")
    now = db.utc_now()
    attempt_status = {"submitted": "confirmed", "already_applied": "confirmed"}.get(status, status)
    db.run("""INSERT INTO application_attempts(application_id,loop_id,engine,status,steps,reason,evidence,created_at)
        VALUES(?,?,?,?,?,?,?,?)""", (application["id"], application.get("loop_id"), engine, attempt_status,
                                     min(max(int(data.get("steps") or 0), 0), 100), reason, evidence, now))
    if status in ("confirmed", "submitted", "already_applied"):
        evidence = evidence or ("Platform shows this job as already applied" if status == "already_applied" else "Confirmation shown")
        db.run("""UPDATE applications SET status='confirmed',confirmation=?,submitted_at=?,notes=?,updated_at=? WHERE id=?""",
               (evidence, now, reason, now, application["id"]))
        result = "confirmed"
    elif status == "external" and str(data.get("url") or "").startswith("https://"):
        url = str(data["url"])[:1000]
        db.run("""UPDATE applications SET status='queued',apply_url=?,channel=?,notes=?,updated_at=? WHERE id=?""",
               (url, channels.channel_for(url), "Continues on employer site", now, application["id"]))
        db.run("UPDATE jobs SET apply_url=? WHERE id=? AND apply_url=''", (url, application["job_id"]))
        result = "queued"
    elif status == "reroute":
        db.run("UPDATE applications SET status='queued',channel='extension',notes=?,updated_at=? WHERE id=?",
               (reason or "Retrying in the candidate's browser", now, application["id"]))
        result = "queued"
    elif status == "prepared":
        db.run("UPDATE applications SET status='prepared',notes=?,updated_at=? WHERE id=?", (reason, now, application["id"]))
        result = "prepared"
    else:
        attempts = application["attempt_count"] + 1
        retry = status in ("error", "timeout") and attempts < MAX_ATTEMPTS
        db.run("UPDATE applications SET status=?,attempt_count=?,notes=?,updated_at=? WHERE id=?",
               ("queued" if retry else "needs_review", attempts, reason or status, now, application["id"]))
        result = "queued" if retry else "needs_review"
    db.log_event("application_result", {"application_id": application["id"], "engine": engine, "status": status, "result": result})
    return {"id": application["id"], "status": result}


def retry(application_id):
    application = db.row("SELECT * FROM applications WHERE id=?", (int(application_id),))
    if not application:
        raise ValueError("Application not found")
    if application["status"] in ("confirmed", "interview", "offer", "hired"):
        raise ValueError("Application was already submitted")
    db.run("UPDATE applications SET status='queued',attempt_count=0,claimed_at='',updated_at=? WHERE id=?",
           (db.utc_now(), application["id"]))
    return {"id": application["id"], "status": "queued"}


def jitter_delay():
    low = settings.number("APPLY_DELAY_MIN_SECONDS", 45)
    high = max(low, settings.number("APPLY_DELAY_MAX_SECONDS", 120))
    return random.uniform(low, high)


def answer(data):
    application = db.row("SELECT * FROM applications WHERE id=?", (int(data.get("application_id") or 0),))
    learner_id = int(data.get("learner_id") or (application["learner_id"] if application else 0))
    job = None
    if application:
        job = db.row("""SELECT jobs.*,companies.name AS company_name FROM jobs LEFT JOIN companies
            ON companies.id=jobs.company_id WHERE jobs.id=?""", (application["job_id"],))
    job = job or data.get("job") or {}
    return questions.answer_fields(learner_id, data.get("fields") or [], job)
