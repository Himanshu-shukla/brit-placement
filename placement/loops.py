"""LoopCV-style reusable job-search configurations and repeatable cycles.

One loop is scoped to exactly one learner. Cycles are idempotent: jobs and
applications are uniquely keyed to prevent recurring searches from spamming.
"""

import json
import threading
from datetime import datetime, timedelta, timezone

from . import applyqueue, db, matching, sources

_tick_lock = threading.Lock()


def _text(value, limit=500):
    return str(value or "").strip()[:limit]


def _titles(data):
    value = data.get("titles") or data.get("title") or ""
    if isinstance(value, str):
        if value.strip().startswith("["):
            try:
                parsed=json.loads(value)
                if isinstance(parsed,list):
                    value=parsed
            except json.JSONDecodeError:
                pass
    if isinstance(value,str):
        result = [t.strip() for t in value.replace("\n", ",").split(",")]
    else:
        result = [str(t).strip() for t in value]
    result = [t for t in result if t][:12]
    if not result:
        raise ValueError("Enter at least one job title")
    return result


def _boards(data):
    value = data.get("boards") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [x.strip() for x in value.split(",")]
    if not isinstance(value, list):
        raise ValueError("Boards must be a list")
    return list(dict.fromkeys(str(x).strip().lower() for x in value if str(x).strip()))[:20]


def create(data):
    learner_id=int(data["learner_id"])
    learner=db.row("SELECT * FROM learners WHERE id=? AND active=1",(learner_id,))
    if not learner:
        raise ValueError("Active learner not found")
    titles=_titles(data)
    boards=_boards(data)
    mode=_text(data.get("mode") or "review").lower()
    if mode not in ("review", "auto"):
        raise ValueError("Mode must be review or auto")
    frequency=int(data.get("frequency_minutes") or 120)
    if frequency < 120 or frequency > 1440:
        raise ValueError("Loop interval must be 120–1440 minutes")
    min_score=int(data.get("min_score") or 60)
    if not 0 <= min_score <= 100:
        raise ValueError("Match threshold must be between 0 and 100")
    cv_version_id=data.get("cv_version_id") or None
    if cv_version_id:
        candidate=db.row("SELECT 1 FROM cv_versions WHERE id=? AND learner_id=?",(int(cv_version_id),learner_id))
        if not candidate:
            raise ValueError("Selected CV belongs to another learner")
    name=_text(data.get("name") or (titles[0]+" · "+learner["name"]),120)
    with db.connection() as conn:
        cursor=conn.execute("""INSERT INTO loops(
         learner_id,name,titles,location,radius_km,remote_only,hybrid_only,boards,experience,job_type,
         min_salary,max_salary,industries,excluded_companies,min_score,mode,cv_version_id,
         email_template,frequency_minutes,active,created_at)
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (learner_id,name,json.dumps(titles),_text(data.get("location") or "United Kingdom",150),
          max(0,min(int(data.get("radius_km") or 30),500)),int(bool(data.get("remote_only"))),
          int(bool(data.get("hybrid_only"))),json.dumps(boards),_text(data.get("experience") or "junior",50),
          _text(data.get("job_type") or "full_time",50),max(0,int(data.get("min_salary") or 0)),
          max(0,int(data.get("max_salary") or 0)),_text(data.get("industries"),500),
          _text(data.get("excluded_companies"),1000),min_score,mode,int(cv_version_id) if cv_version_id else None,
          _text(data.get("email_template"),2000),frequency,int(bool(data.get("active",True))),db.utc_now()))
        loop_id=cursor.lastrowid
        conn.execute("UPDATE loops SET auto_email=?,tailor_cv=? WHERE id=?",
                     (int(_flag(data.get("auto_email",True))),int(_flag(data.get("tailor_cv",True))),loop_id))
    db.log_event("loop_created",{"loop_id":loop_id,"learner_id":learner_id,"mode":mode})
    return db.row("SELECT * FROM loops WHERE id=?",(loop_id,))


def _flag(value):
    return value in (True,1,"1","true","on","yes")


def update(loop_id, data):
    loop=db.row("SELECT * FROM loops WHERE id=?",(int(loop_id),))
    if not loop:
        raise ValueError("Loop not found")
    # Reuse the complete validation path without replacing or deleting the loop.
    merged={**loop,**data}
    titles=_titles(merged)
    boards=_boards(merged)
    mode=_text(merged.get("mode") or "review").lower()
    frequency=int(merged.get("frequency_minutes") or 120)
    score=int(merged.get("min_score") or 60)
    if mode not in ("review","auto") or not 120 <= frequency <= 1440 or not 0 <= score <= 100:
        raise ValueError("Invalid mode, frequency or match threshold")
    with db.connection() as conn:
        conn.execute("""UPDATE loops SET name=?,titles=?,location=?,remote_only=?,hybrid_only=?,
         boards=?,min_score=?,mode=?,frequency_minutes=?,active=?,email_template=?,excluded_companies=?,
         auto_email=?,tailor_cv=?,experience=? WHERE id=?""",
         (_text(merged["name"],120),json.dumps(titles),_text(merged["location"],150),
          int(_flag(merged["remote_only"])),int(_flag(merged["hybrid_only"])),json.dumps(boards),
          score,mode,frequency,int(_flag(merged["active"])),_text(merged.get("email_template"),2000),
          _text(merged.get("excluded_companies"),1000),int(_flag(merged.get("auto_email",1))),
          int(_flag(merged.get("tailor_cv",1))),_text(merged.get("experience") or "junior",50),int(loop_id)))
    return db.row("SELECT * FROM loops WHERE id=?",(int(loop_id),))


SENIORITY={"junior","graduate","senior","entry","level","trainee","apprentice","lead","principal","mid","intern","internship"}


def title_matches(target, job_title):
    """All meaningful words of a target title appear in the job title ("Junior Data Analyst" ~ "Data Analyst II")."""
    wanted=[w for w in matching.normalize(target).split() if w not in SENIORITY]
    have=set(matching.normalize(job_title).split())
    return bool(wanted) and all(w in have for w in wanted)


def _eligible(loop, job, learner, sponsor_names):
    titles=json.loads(loop["titles"])
    if not any(title_matches(title, job["title"]) for title in titles):
        return 0,"excluded","Outside requested job titles"
    boards=[b for b in json.loads(loop["boards"]) if b]
    source=job["source"].lower()
    if boards and "all" not in boards and not any(source==x or source=="csv-"+x for x in boards):
        return 0,"excluded","Job source excluded by this loop"
    text=(str(job.get("location") or "")+" "+str(job.get("description") or "")).lower()
    if loop["remote_only"] and "remote" not in text:
        return 0,"excluded","Remote-only preference"
    if loop["hybrid_only"] and "hybrid" not in text:
        return 0,"excluded","Hybrid-only preference"
    company=matching.normalize(job.get("company_name") or "")
    excluded={matching.normalize(x) for x in loop["excluded_companies"].split(",") if x.strip()}
    if company in excluded:
        return 0,"excluded","Employer excluded by the learner"
    desired=matching.normalize(loop["location"])
    actual=matching.normalize(job.get("location") or "")
    if desired not in ("united kingdom","uk","any","remote","") and actual and desired not in actual and "remote" not in actual:
        return 0,"review","Location outside preferred area; confirm commute or remote eligibility"
    score,decision,reasons=matching.calculate(learner,job,sponsor_names,loop_titles=True,
                                              allow_senior=loop["experience"] in ("mid","senior","all"))
    if decision=="matched" and score < loop["min_score"]:
        decision="review"
        reasons.append("Below loop match threshold")
    return score,decision,"; ".join(reasons)


def run(loop_id, discover=False, max_queue=12):
    loop=db.row("SELECT * FROM loops WHERE id=?",(int(loop_id),))
    if not loop:
        raise ValueError("Loop not found")
    learner=db.row("SELECT * FROM learners WHERE id=?",(loop["learner_id"],))
    if not learner or not learner["active"]:
        raise ValueError("Active learner required")
    discovery={}
    if discover:
        discovery["employers"]=sources.sync_companies()
        discovery["boards"]=sources.search_for_loops([loop])
    jobs=db.rows("SELECT j.*,c.name AS company_name FROM jobs j LEFT JOIN companies c ON c.id=j.company_id WHERE j.active=1")
    sponsor_names={s["normalized_name"] for s in db.rows("SELECT normalized_name FROM sponsors")}
    now=datetime.now(timezone.utc)
    stats={"loop_id":loop["id"],"scanned":len(jobs),"matched":0,"review":0,"queued":0,"already_queued":0,"discovery":discovery}
    queued=0
    with db.connection() as conn:
        for job in jobs:
            score,decision,reason=_eligible(loop,job,learner,sponsor_names)
            if decision=="excluded":
                continue
            stats["matched" if decision=="matched" else "review"]+=1
            record=conn.execute("SELECT status FROM loop_jobs WHERE loop_id=? AND job_id=?",(loop["id"],job["id"])).fetchone()
            if record and record["status"] in ("dismissed","applied","favorite"):
                continue
            conn.execute("""INSERT INTO loop_jobs(loop_id,job_id,score,status,reason,first_seen_at,updated_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(loop_id,job_id) DO UPDATE SET
              score=excluded.score,reason=excluded.reason,updated_at=excluded.updated_at""",
              (loop["id"],job["id"],score,decision,reason,db.utc_now(),db.utc_now()))
            if (loop["mode"]=="auto" and decision=="matched" and learner["permission_to_apply"] and queued<max_queue):
                if not applyqueue.enqueue(conn,learner["id"],job,"loop_auto",loop["id"]):
                    stats["already_queued"]+=1
                    continue
                conn.execute("UPDATE loop_jobs SET status='queued' WHERE loop_id=? AND job_id=?",(loop["id"],job["id"]))
                queued+=1
        next_due=(now+timedelta(minutes=loop["frequency_minutes"])).isoformat(timespec="seconds")
        conn.execute("""UPDATE loops SET last_run_at=?,next_run_at=?,last_error='',run_count=run_count+1 WHERE id=?""",
                     (now.isoformat(timespec="seconds"),next_due,loop["id"]))
    stats["queued"]=queued
    db.log_event("loop_run",stats)
    return stats


def tick(discover=True):
    """Run due loops with a process-local lock; safe for a single-instance host."""
    if not _tick_lock.acquire(blocking=False):
        return {"skipped":"scheduler already running"}
    try:
        now=db.utc_now()
        due=db.rows("SELECT * FROM loops WHERE active=1 AND (next_run_at='' OR next_run_at<=?) ORDER BY id LIMIT 50",(now,))
        if not due:
            return {"due":0,"runs":[],"errors":[]}
        discovery={}
        if discover:
            try:
                discovery["companies"]=sources.sync_companies()
                # One search per distinct title set across cohorts rather than once per loop.
                discovery["boards"]=sources.search_for_loops(due)
            except Exception as exc:
                discovery["error"]=str(exc)[:200]
        result={"due":len(due),"runs":[],"errors":[],"discovery":discovery}
        for item in due:
            try:
                result["runs"].append(run(item["id"],discover=False))
            except Exception as exc:
                message=str(exc)[:300]
                db.run("UPDATE loops SET last_error=?,next_run_at=? WHERE id=?",
                       (message,(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec="seconds"),item["id"]))
                result["errors"].append({"loop_id":item["id"],"message":message})
        return result
    finally:
        _tick_lock.release()


def run_all():
    """Re-score every active loop against jobs already found (e.g. after a browser board search)."""
    results=[]
    for loop in db.rows("SELECT id FROM loops WHERE active=1"):
        try:
            results.append(run(loop["id"]))
        except ValueError as exc:
            results.append({"loop_id":loop["id"],"error":str(exc)})
    return {"runs":results}
