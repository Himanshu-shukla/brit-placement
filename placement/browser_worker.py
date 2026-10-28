"""Optional form prefiller for employer-hosted application pages.

The browser always pauses BEFORE submit. No authentication bypass or captcha solving.
"""

import argparse
import json
from urllib.parse import urlsplit

from . import db


def prepare(application_id):
    record = db.row("""SELECT a.*,j.url,j.source,j.title,l.name,l.email,l.phone,l.location,l.cv_path,l.permission_to_apply
    FROM applications a JOIN jobs j ON j.id=a.job_id
    JOIN learners l ON l.id=a.learner_id WHERE a.id=?""", (int(application_id),))
    if not record:
        raise ValueError("Unknown application")
    if not record["permission_to_apply"]:
        raise ValueError("Candidate permission has not been recorded")
    url = urlsplit(record["url"])
    if url.scheme != "https" or not url.hostname:
        raise ValueError("Only HTTPS employer applications may be assisted")
    if url.hostname.lower().removeprefix("www.") in ("linkedin.com", "indeed.com", "reed.co.uk", "adzuna.co.uk"):
        raise ValueError("Use the Chrome extension review workflow on job-board sites")
    return record


def fill(application_id):
    record = prepare(application_id)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install optional Playwright: pip install playwright && playwright install chromium") from exc
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(record["url"], wait_until="domcontentloaded", timeout=30000)
        fields = {
            'input[name="name"]': record["name"],
            'input[name="full_name"]': record["name"],
            'input[name="email"]': record["email"],
            'input[type="email"]': record["email"],
            'input[name="phone"]': record["phone"],
        }
        for selector, value in fields.items():
            if value:
                field = page.locator(selector).first
                try:
                    if field.is_visible(timeout=1500):
                        field.fill(value, timeout=1500)
                except Exception:
                    pass
        if record["cv_path"]:
            try:
                upload = page.locator('input[type="file"]').first
                if upload.count():
                    upload.set_input_files(record["cv_path"])
            except Exception:
                pass
        print("Form prefilling attempted. Check ALL fields; complete questions, authentication and submission yourself.")
        input("Press Enter AFTER submitting or to close browser (no automatic status change)...")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("application_id", type=int)
    fill(parser.parse_args().application_id)
