"""Server-side automatic applications on employer / ATS pages (Greenhouse, Lever, Ashby,
Workable, SmartRecruiters, company career sites...).

Runs the same apply engine as the Chrome extension (extension/engine.js) inside Playwright
Chromium. Job boards that need the candidate's own login (LinkedIn, Indeed, Glassdoor, Reed,
Totaljobs...) are routed to the extension instead, and pages that show a CAPTCHA or a login
are handed to the extension to retry in the candidate's real browser.
"""

import random
from urllib.parse import urlsplit

from . import applyqueue, channels, config, db, questions, settings

ENGINE = config.ROOT / "extension" / "engine.js"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


def _allowed_host(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or channels.needs_login(url):
        return False
    allowed = {d.strip().lower() for d in settings.get("AUTO_APPLY_ALLOWED_HOSTS").split(",") if d.strip()}
    return "*" in allowed or host in allowed or any(host.endswith("." + d.lstrip("*.")) for d in allowed if d.startswith("*."))


def _record(application_id):
    item = db.row("""SELECT a.*,j.url,j.title,j.description,l.name,l.email,l.phone,l.location,
      l.permission_to_apply,l.cv_path,l.id AS candidate_id
      FROM applications a JOIN jobs j ON j.id=a.job_id
      JOIN learners l ON l.id=a.learner_id WHERE a.id=?""", (int(application_id),))
    if not item:
        raise ValueError("Application not found")
    if not item["permission_to_apply"]:
        raise ValueError("Candidate consent is required")
    if item["status"] not in ("queued", "prepared", "applying", "needs_review"):
        raise ValueError("Application is not queued")
    if not _allowed_host(item.get("apply_url") or item["url"]):
        raise ValueError("This page needs the candidate's own login; it is applied by the Chrome extension")
    return item


def _preferred_cv(item, loop_id=None):
    """Loop-selected CV, then the learner's default (used when tailoring is off)."""
    import os
    if loop_id:
        cv = db.row("""SELECT v.path FROM loops l JOIN cv_versions v ON v.id=l.cv_version_id
           WHERE l.id=? AND l.learner_id=?""", (int(loop_id), item["candidate_id"]))
    else:
        cv = db.row("""SELECT v.path FROM loops l JOIN loop_jobs lj ON lj.loop_id=l.id
           JOIN cv_versions v ON v.id=l.cv_version_id
           WHERE l.learner_id=? AND lj.job_id=? AND l.mode='auto'
           ORDER BY l.id DESC LIMIT 1""", (item["candidate_id"], item["job_id"]))
    if cv and os.path.isfile(cv["path"]):
        return cv["path"]
    if item["cv_path"] and os.path.isfile(item["cv_path"]):
        return item["cv_path"]
    versions = db.rows("SELECT path FROM cv_versions WHERE learner_id=? ORDER BY is_default DESC,id DESC", (item["candidate_id"],))
    return next((v["path"] for v in versions if os.path.isfile(v["path"])), None)


OPTIONS_JS = """() => [...document.querySelectorAll('[role="option"]')]
  .filter(o => o.getBoundingClientRect().height > 0).map(o => o.innerText.trim()).filter(Boolean).slice(0, 80)"""


def _read_options(page, selector):
    """Open a custom dropdown with a real click and read its options."""
    try:
        page.click(selector, timeout=4000)
        page.wait_for_timeout(400)
        options = page.evaluate(OPTIONS_JS)
        page.keyboard.press("Escape")
        return options
    except Exception:
        return []


def _trusted_fill(page, selector, value):
    """Type into a custom dropdown and pick the matching option with real keyboard input."""
    try:
        page.click(selector, timeout=4000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.type(value[:60], delay=15)
        page.wait_for_timeout(600)
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
    except Exception:
        pass


def _drive(page, context, ctx, engine_source, max_rounds=30):
    """Loop the engine over a Playwright page until it reaches a final state."""
    idle, steps = 0, 0
    for _ in range(max_rounds):
        if len(context.pages) > 1 and context.pages[-1] is not page:
            page = context.pages[-1]
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(1200 + random.randint(0, 1200))
        if channels.needs_login(page.url):
            return {"status": "reroute", "reason": f"Continues on {channels.host_of(page.url)}, which needs the candidate's login", "steps": steps}
        if not _allowed_host(page.url):
            return {"status": "needs_review", "reason": f"Redirected to non-allowed host {channels.host_of(page.url)}", "steps": steps}
        try:
            if not page.evaluate("() => !!(window.__britEngine && window.__britEngine.version === 3)"):
                page.add_script_tag(content=engine_source)
            result = page.evaluate("ctx => window.__britEngine.step(ctx)", ctx)
        except Exception as exc:  # navigation during evaluate
            result = {"state": "navigating", "reason": str(exc)[:200]}
        result = result or {"state": "navigating"}
        steps += 1
        state = result.get("state")
        if state == "submitted":
            return {"status": "confirmed", "evidence": result.get("evidence"), "steps": steps}
        if state == "already_applied":
            return {"status": "already_applied", "evidence": result.get("evidence"), "steps": steps}
        if state == "ready":
            return {"status": "prepared", "reason": "Filled; submission disabled", "steps": steps}
        if state == "external":
            page.goto(result["url"], wait_until="domcontentloaded", timeout=45000)
            idle = 0
            continue
        if state == "need_options":
            for selector in result.get("selectors", [])[:30]:
                ctx["comboOptions"][selector] = _read_options(page, selector)
            continue
        if state == "trusted_fill":
            for fill in result.get("fills", [])[:30]:
                _trusted_fill(page, fill["selector"], fill["value"])
            continue
        if state in ("continue", "navigating"):
            if result.get("clicked") == "submit":
                ctx["afterSubmit"] = True
            idle = 0
            continue
        if state == "no_form":
            idle += 1
            if idle >= (2 if ctx.get("afterSubmit") else 3):
                reason = result.get("reason") or "No application form"
                return {"status": "needs_review", "reason": reason, "steps": steps}
            continue
        if state in ("blocked", "login_required"):
            return {"status": "reroute", "reason": result.get("reason") or state, "steps": steps}
        return {"status": "needs_review", "reason": f"{state}: {result.get('reason', '')}", "unknown": result.get("unknown"), "steps": steps}
    return {"status": "needs_review", "reason": "Too many steps without finishing", "steps": steps}


def process(application_id, loop_id=None, submit=False):
    item = _record(application_id)
    if submit and not settings.enabled("AUTO_SUBMIT_ENABLED"):
        raise ValueError("Turn on automatic submission in Settings")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install Playwright and Chromium to enable server-side applications") from exc
    if item["status"] != "applying":
        db.run("UPDATE applications SET status='applying',claimed_at=?,updated_at=? WHERE id=?",
               (db.utc_now(), db.utc_now(), item["id"]))
    data = applyqueue.payload(dict(item))
    ctx = {"profile": data["profile"], "answers": data["answers"], "cv": data["cv"], "job": data["job"],
           "applicationId": item["id"], "submit": bool(submit), "afterSubmit": False,
           "trusted": True, "comboOptions": {}}
    learner_id = item["learner_id"]
    engine_source = ENGINE.read_text(encoding="utf-8")
    try:
        with sync_playwright() as api:
            browser = api.chromium.launch(headless=settings.enabled("SERVER_HEADLESS"))
            context = browser.new_context(user_agent=UA, locale="en-GB", timezone_id="Europe/London",
                                          viewport={"width": 1366, "height": 900}, bypass_csp=True)
            context.expose_function("__britAsk", lambda payload: questions.answer_fields(
                learner_id, (payload or {}).get("fields") or [], data["job"]))
            page = context.new_page()
            page.goto(data["application"]["url"], wait_until="domcontentloaded", timeout=45000)
            outcome = _drive(page, context, ctx, engine_source)
            browser.close()
    except Exception as exc:
        outcome = {"status": "error", "reason": str(exc)[:300]}
    saved = applyqueue.record(item["id"], outcome, engine="playwright")
    return {**outcome, "result": saved["status"]}


def process_queue(limit=4):
    if not settings.enabled("AUTO_SUBMIT_ENABLED"):
        return {"processed": 0, "reason": "Automatic submission is turned off in Settings"}
    applyqueue.release_stale()
    items = db.rows("""SELECT a.id,a.learner_id,COALESCE(NULLIF(a.apply_url,''),j.url) AS url FROM applications a
      JOIN jobs j ON j.id=a.job_id JOIN learners l ON l.id=a.learner_id
      WHERE a.status='queued' AND a.channel IN ('server','') AND l.permission_to_apply=1
        AND NOT EXISTS (SELECT 1 FROM application_attempts at WHERE at.application_id=a.id AND at.engine='playwright')
      ORDER BY a.id LIMIT 100""")
    daily = settings.number("DAILY_APPLY_LIMIT", 40)
    results = []
    for item in items:
        if len(results) >= max(0, min(int(limit), 10)):
            break
        if not _allowed_host(item["url"]):
            db.run("UPDATE applications SET channel='extension' WHERE id=?", (item["id"],))
            continue
        if applyqueue.applied_today(item["learner_id"]) >= daily:
            continue
        try:
            outcome = process(item["id"], submit=True)
        except Exception as exc:
            # One failure must not block other candidates; it is recorded and not retried here.
            db.run("""INSERT INTO application_attempts(application_id,engine,status,reason,created_at)
                      VALUES(?, 'playwright','error',?,?)""", (item["id"], str(exc)[:900], db.utc_now()))
            db.run("UPDATE applications SET status='needs_review',notes=? WHERE id=? AND status IN ('queued','applying')",
                   (str(exc)[:900], item["id"]))
            outcome = {"status": "error", "reason": str(exc)[:250]}
        results.append({"application_id": item["id"], **outcome})
    return {"processed": len(results), "results": results}
