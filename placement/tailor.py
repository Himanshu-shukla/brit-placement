"""Per-job tailored CVs (LoopCV-style): reorder and rephrase the candidate's real facts
around the job's keywords. Employers, qualifications and skills that do not appear in the
candidate's own data are dropped, so tailoring cannot invent experience."""

import json
import re

from . import ai, cv, cv_files, db, matching, settings

SYSTEM = (
    "You are an expert UK CV writer. Rewrite the candidate's CV for the target job so it passes ATS keyword "
    "screening and reads well to a recruiter. Use ONLY facts in the candidate data: never invent employers, job "
    "titles, dates, degrees, certifications, tools or metrics. You may reorder, rephrase, emphasise and mirror the "
    "job's wording where the candidate genuinely has that skill.\n"
    "Return JSON: {\"headline\": str, \"summary\": str (60-90 words), \"skills\": [str] (most relevant first, max 16), "
    "\"experience\": [{\"title\": str, \"organisation\": str, \"dates\": str, \"bullets\": [str]}], "
    "\"projects\": [{\"title\": str, \"organisation\": str, \"dates\": str, \"bullets\": [str]}], "
    "\"education\": [{\"qualification\": str, \"institution\": str, \"dates\": str}], \"keywords\": [str]}"
)


def _norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _source_text(learner):
    """Everything the candidate has told us, including their uploaded master CV."""
    parts = [str(learner.get(k) or "") for k in ("summary", "skills", "projects", "work_history", "education", "extra_facts")]
    upload = db.row("""SELECT * FROM cv_versions WHERE learner_id=? AND filename NOT LIKE 'generated-cv%'
        AND id NOT IN (SELECT cv_version_id FROM tailored_cvs) ORDER BY is_default DESC,id DESC LIMIT 1""",
                    (learner["id"],))
    if upload:
        try:
            info, binary = cv_files.read_variant(upload["id"])
            parts.append(cv_files.extract_text(info, binary))
        except (ValueError, OSError):
            pass
    return "\n".join(p for p in parts if p.strip())[:24000]


def _grounded(content, source):
    """Drop anything whose organisation/qualification/skill is absent from the source facts."""
    haystack = " " + _norm(source) + " "
    present = lambda value: not _norm(value) or (" " + _norm(value) + " ") in haystack
    content["skills"] = [s for s in content.get("skills") or [] if isinstance(s, str) and present(s)][:16]
    for key in ("experience", "projects"):
        content[key] = [e for e in content.get(key) or [] if isinstance(e, dict) and present(e.get("organisation"))]
    content["education"] = [e for e in content.get("education") or []
                            if isinstance(e, dict) and present(e.get("institution"))]
    return content


def _fallback(learner, job):
    """Deterministic tailoring without AI: put the job's skills first."""
    skills = [s.strip() for s in str(learner.get("skills") or "").split(",") if s.strip()]
    wanted = matching.skills_in(f"{job.get('title', '')} {job.get('description', '')}")
    skills.sort(key=lambda s: 0 if matching.skills_in(s) & wanted else 1)
    return {"headline": job.get("title", ""), "summary": learner.get("summary") or "", "skills": skills,
            "experience": learner.get("work_history") or "", "projects": learner.get("projects") or "",
            "education": learner.get("education") or "", "keywords": sorted(wanted)}


def for_job(learner_id, job_id, force=False):
    learner = db.row("SELECT * FROM learners WHERE id=?", (int(learner_id),))
    job = db.row("""SELECT jobs.*,companies.name AS company_name FROM jobs
        LEFT JOIN companies ON companies.id=jobs.company_id WHERE jobs.id=?""", (int(job_id),))
    if not learner or not job:
        raise ValueError("Learner or job not found")
    cached = db.row("SELECT * FROM tailored_cvs WHERE learner_id=? AND job_id=?", (learner["id"], job["id"]))
    if cached and not force:
        version = db.row("SELECT * FROM cv_versions WHERE id=?", (cached["cv_version_id"],))
        if version:
            return version
    source = _source_text(learner)
    content = None
    if ai.available():
        try:
            content = ai.ask_json(SYSTEM, {
                "candidate": {"name": learner["name"], "location": learner["location"],
                              "experience_years": learner["experience_years"], "facts": source},
                "job": {"title": job["title"], "company": job.get("company_name") or "",
                        "description": str(job.get("description") or "")[:6000]}}, max_tokens=3500)
            content = _grounded(content, source + " " + str(learner.get("skills") or ""))
            if not content.get("skills") and not content.get("summary"):
                content = None
        except ValueError as exc:
            db.log_event("tailor_error", {"job_id": job["id"], "error": str(exc)[:300]})
    if content is None:
        content = _fallback(learner, job)
    content["name"] = learner["name"]
    content["contact"] = [learner["email"], learner["phone"], learner["location"], learner.get("linkedin_url"),
                          learner.get("portfolio_url")]
    if not content.get("experience") and learner.get("work_history"):
        content["experience"] = learner["work_history"]
    if not content.get("projects") and learner.get("projects"):
        content["projects"] = learner["projects"]
    if not content.get("education") and learner.get("education"):
        content["education"] = learner["education"]
    pdf = cv.render_pdf(content)
    filename = re.sub(r"[^A-Za-z0-9]+", "_", learner["name"]).strip("_") + "_CV.pdf"
    label = f"Tailored · {job['title'][:50]} · {(job.get('company_name') or '')[:30]}"
    version = cv_files._persist(learner["id"], label, filename, "application/pdf", pdf, make_default=False)
    keywords = json.dumps(content.get("keywords") or [])[:2000]
    with db.connection() as conn:
        conn.execute("""INSERT INTO tailored_cvs(learner_id,job_id,cv_version_id,keywords,created_at)
          VALUES(?,?,?,?,?) ON CONFLICT(learner_id,job_id) DO UPDATE SET
          cv_version_id=excluded.cv_version_id,keywords=excluded.keywords,created_at=excluded.created_at""",
                     (learner["id"], job["id"], version["id"], keywords, db.utc_now()))
    db.log_event("cv_tailored", {"learner_id": learner["id"], "job_id": job["id"], "cv_version_id": version["id"]})
    return version


def cv_for_application(application, loop=None):
    """The CV file to send: tailored when enabled, else the loop's chosen CV, else the default."""
    if settings.enabled("TAILOR_CV") and (loop is None or loop.get("tailor_cv", 1)):
        try:
            return for_job(application["learner_id"], application["job_id"])
        except (RuntimeError, ValueError) as exc:
            db.log_event("tailor_skipped", {"application_id": application["id"], "error": str(exc)[:200]})
    if loop and loop.get("cv_version_id"):
        version = db.row("SELECT * FROM cv_versions WHERE id=?", (loop["cv_version_id"],))
        if version:
            return version
    version = db.row("SELECT * FROM cv_versions WHERE learner_id=? ORDER BY is_default DESC,id DESC LIMIT 1",
                     (application["learner_id"],))
    if version:
        return version
    return cv_files.generate(application["learner_id"], "Generated master CV")
