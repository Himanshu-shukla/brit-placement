"""Conservative unattended applicant automation for explicitly allowed employer sites.

Requires Playwright, a configured employer-domain allowlist and explicit opt-in.
Does not automate LinkedIn/Indeed/Glassdoor, solve challenges, fabricate answers,
or mark success without a visible confirmation.
"""

import os
import re
from urllib.parse import urlsplit

from . import db, cv_files, questions

CONFIRMATION_TERMS=("application submitted","application received","thank you for applying",
                    "thanks for applying","we have received your application","successfully applied")


def _allowed_host(url):
    parsed=urlsplit(url)
    host=(parsed.hostname or "").lower()
    if parsed.scheme!="https" or not host:
        return False
    if any(host==domain or host.endswith("."+domain) for domain in RESTRICTED):
        return False
    allowed={domain.strip().lower() for domain in os.getenv("AUTO_APPLY_ALLOWED_HOSTS","").split(",") if domain.strip()}
    # Exact hostname allowlist avoids unexpected submission to unrelated tenants.
    return host in allowed


def _record(application_id):
    item=db.row("""SELECT a.*,j.url,j.title,j.description,l.name,l.email,l.phone,l.location,
      l.permission_to_apply,l.cv_path,l.id AS candidate_id
      FROM applications a JOIN jobs j ON j.id=a.job_id
      JOIN learners l ON l.id=a.learner_id WHERE a.id=?""",(int(application_id),))
    if not item:raise ValueError("Application not found")
    if not item["permission_to_apply"]:raise ValueError("Candidate consent is required")
    if item["status"] not in ("queued","prepared"):
        raise ValueError("Application is not queued or prepared")
    if not _allowed_host(item["url"]):
        raise ValueError("Employer hostname is not explicitly allowed for autonomous submission")
    return item


def _attempt(application_id,loop_id,status,steps=0,reason="",evidence=""):
    db.run("""INSERT INTO application_attempts(application_id,loop_id,engine,status,steps,reason,evidence,created_at)
              VALUES(?,?,'playwright',?,?,?,?,?)""",
           (int(application_id),loop_id,status,steps,reason[:900],evidence[:900],db.utc_now()))


def _answer_map(learner_id):
    return {item["normalized_question"]:item["answer"] for item in db.rows(
        "SELECT normalized_question,answer FROM question_answers WHERE learner_id=? AND approved=1",
        (int(learner_id),))}


def _preferred_cv(item, loop_id=None):
    """Respect role-specific CVs attached to the application search loop."""
    if loop_id:
        cv=db.row("""SELECT v.path FROM loops l JOIN cv_versions v ON v.id=l.cv_version_id
           WHERE l.id=? AND l.learner_id=?""",(int(loop_id),item["candidate_id"]))
    else:
        cv=db.row("""SELECT v.path FROM loops l JOIN loop_jobs lj ON lj.loop_id=l.id
           JOIN cv_versions v ON v.id=l.cv_version_id
           WHERE l.learner_id=? AND lj.job_id=? AND l.mode='auto'
           ORDER BY l.id DESC LIMIT 1""",(item["candidate_id"],item["job_id"]))
    if cv and os.path.isfile(cv["path"]):
        return cv["path"]
    path=item["cv_path"]
    if path and os.path.isfile(path):
        return path
    versions=db.rows("SELECT path FROM cv_versions WHERE learner_id=? ORDER BY is_default DESC,id DESC",
                     (item["candidate_id"],))
    return next((v["path"] for v in versions if os.path.isfile(v["path"])),None)


def process(application_id,loop_id=None,submit=False):
    item=_record(application_id)
    if submit and os.getenv("AUTO_SUBMIT_ENABLED")!="1":
        raise ValueError("Set AUTO_SUBMIT_ENABLED=1 to explicitly allow unattended submissions")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install Playwright and Chromium to enable ATS automation") from exc
    cv_path=_preferred_cv(item,loop_id)
    if not cv_path:
        raise ValueError("Candidate has no uploaded/validated CV file")
    steps=0
    try:
        with sync_playwright() as api:
            browser=api.chromium.launch(headless=True)
            page=browser.new_page(accept_downloads=False)
            page.goto(item["url"],wait_until="domcontentloaded",timeout=30000)
            # Approved entry URLs may redirect to a shared ATS, authentication
            # provider, or an entirely unrelated domain. Recheck the actual
            # destination before reading private candidate fields or submitting.
            if not _allowed_host(page.url):
                _attempt(application_id,loop_id,"blocked",reason="Redirected outside the approved employer hostname")
                return {"status":"blocked","reason":"Redirected outside approved employer hostname","steps":0}
            if any(x in page.title().lower() for x in ("captcha","verify you are human","sign in")):
                _attempt(application_id,loop_id,"blocked",reason="Authentication or bot challenge on opening page")
                return {"status":"blocked","reason":"Authentication or site challenge","steps":0}
            values={"name":item["name"],"email":item["email"],"phone":item["phone"],"location":item["location"]}
            name_parts=item["name"].split()
            values["first_name"]=name_parts[0] if name_parts else ""
            values["last_name"]=" ".join(name_parts[1:])
            labels={
                "name":["Full name","Your name","Applicant name"],
                "first_name":["First name","Given name"],
                "last_name":["Last name","Surname","Family name"],
                "email":["Email","Email address"],
                "phone":["Phone","Phone number","Mobile","Telephone"],
                "location":["Current location","City"]
            }
            for key,examples in labels.items():
                if not values[key]:continue
                for label in examples:
                    field=page.get_by_label(label,exact=True).first
                    try:
                        if field.count() and field.is_visible():
                            field.fill(values[key],timeout=1000)
                            steps+=1
                    except Exception:
                        continue
            for selector,key in (('input[type="email"]',"email"),('input[type="tel"]',"phone")):
                element=page.locator(selector).first
                try:
                    if element.count() and element.is_visible() and not element.input_value():
                        element.fill(values[key],timeout=1000)
                        steps+=1
                except Exception:
                    pass
            uploader=page.locator('input[type="file"]').first
            if uploader.count():
                uploader.set_input_files(cv_path,timeout=5000)
                steps+=1
            # Fill only answers previously approved by this exact candidate.
            answer_map=_answer_map(item["candidate_id"])
            for field in page.locator('textarea, input[type="text"]').all()[:35]:
                try:
                    if not field.is_visible() or field.input_value():continue
                    label_text=field.evaluate("e => (e.labels && e.labels[0] && e.labels[0].innerText) || e.getAttribute('aria-label') || ''")
                    key=questions.normalize(label_text)
                    if key in answer_map and answer_map[key]:
                        field.fill(answer_map[key],timeout=1000)
                        steps+=1
                except Exception:
                    continue
            challenge=page.locator('iframe[src*="captcha"],iframe[src*="recaptcha"],input[name*="captcha"]').count()>0
            if challenge:
                _attempt(application_id,loop_id,"blocked",steps,"CAPTCHA/challenge encountered")
                return {"status":"blocked","steps":steps,"reason":"CAPTCHA/challenge encountered"}
            # Never guess unanswered required, custom consent, visa, or salary inputs.
            missing=page.locator('input:required:not([type="hidden"]),textarea:required,select:required').evaluate_all("""
              els => els.filter(e=>!e.disabled && e.getClientRects().length && !e.checkValidity())
                .map(e => e.name || e.id || e.getAttribute('aria-label') || e.type).slice(0,12)
            """)
            if missing:
                _attempt(application_id,loop_id,"needs_review",steps,"Required questions: "+", ".join(missing))
                db.run("UPDATE applications SET status='prepared',updated_at=? WHERE id=?",(db.utc_now(),application_id))
                return {"status":"needs_review","steps":steps,"unanswered":missing}
            if not submit:
                _attempt(application_id,loop_id,"prepared",steps,"Submission disabled; prefilling completed")
                db.run("UPDATE applications SET status='prepared',updated_at=? WHERE id=?",(db.utc_now(),application_id))
                return {"status":"prepared","steps":steps}
            button=page.get_by_role("button",name=re.compile(r"^(submit application|apply now|submit|send application)$",re.I)).first
            if not button.count() or not button.is_visible() or not button.is_enabled():
                _attempt(application_id,loop_id,"needs_review",steps,"No unambiguous submit control")
                return {"status":"needs_review","steps":steps,"reason":"Submit control not identified"}
            if not _allowed_host(page.url):
                _attempt(application_id,loop_id,"blocked",steps,"Application page changed to an unapproved hostname")
                return {"status":"blocked","steps":steps,"reason":"Application page hostname changed"}
            button.click(timeout=5000)
            page.wait_for_timeout(2500)
            text=(page.locator("body").inner_text(timeout=3000) or "").lower()
            if any(term in text for term in CONFIRMATION_TERMS):
                evidence="Employer page displayed submission confirmation"
                db.run("UPDATE applications SET status='confirmed',confirmation=?,updated_at=? WHERE id=?",
                       (evidence,db.utc_now(),application_id))
                _attempt(application_id,loop_id,"confirmed",steps+1,evidence,evidence)
                return {"status":"confirmed","steps":steps+1,"evidence":evidence}
            # A submit was clicked, so unknown outcome: NEVER silently retry.
            db.run("UPDATE applications SET status='prepared',notes=?,updated_at=? WHERE id=?",
                   ("Submit clicked but no confirmation found; verify manually before retrying",db.utc_now(),application_id))
            _attempt(application_id,loop_id,"unverified",steps+1,"Submit attempted; no receipt detected")
            return {"status":"unverified","steps":steps+1,"reason":"Manual verification required"}
    except Exception as exc:
        _attempt(application_id,loop_id,"error",steps,str(exc))
        return {"status":"error","steps":steps,"reason":str(exc)[:250]}


def process_queue(limit=4):
    if os.getenv("AUTO_SUBMIT_ENABLED")!="1":
        return {"processed":0,"reason":"AUTO_SUBMIT_ENABLED is not set"}
    items=db.rows("""SELECT a.id,j.url FROM applications a JOIN jobs j ON j.id=a.job_id
      WHERE a.status='queued' AND a.method='loop_auto'
        AND NOT EXISTS (SELECT 1 FROM application_attempts at WHERE at.application_id=a.id)
      ORDER BY a.id LIMIT 100""")
    results=[]
    for item in items:
        if len(results)>=max(0,min(int(limit),10)):break
        if _allowed_host(item["url"]):
            try:
                outcome=process(item["id"],submit=True)
            except Exception as exc:
                # A candidate missing a CV must not prevent other candidates
                # from being processed. A failure needs manual intervention
                # before another autonomous submission attempt is authorised.
                _attempt(item["id"],None,"error",reason=str(exc))
                outcome={"status":"error","reason":str(exc)[:250]}
            results.append({"application_id":item["id"],**outcome})
    return {"processed":len(results),"results":results}
