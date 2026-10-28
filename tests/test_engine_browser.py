"""Real-browser tests for the apply engine (extension/engine.js) driven by the server's
Playwright path (autoapply.process). Uses local fixture pages that mimic a multi-step
company ATS form and a LinkedIn-style Easy Apply dialog. AI calls are stubbed."""

import json
import os
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_workflows import TempStorage
from placement import ai, applyqueue, autoapply, channels, db, outreach, recruiters, settings, tailor

FIXTURES = Path(__file__).parent / "fixtures"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).parent.parent / "data" / "playwright"))


def fake_ai(system, payload, **kwargs):
    """Deterministic stand-in for Groq."""
    if "CV writer" in system:
        return {"headline": "Junior Data Analyst", "summary": "Analytics learner skilled in SQL and Power BI.",
                "skills": ["SQL", "Power BI", "Excel", "Kubernetes"],
                "experience": [{"title": "Analyst", "organisation": "Invented Corp", "dates": "2020", "bullets": ["x"]}],
                "projects": [{"title": "Sales dashboard", "organisation": "", "dates": "", "bullets": ["Built a SQL dashboard"]}],
                "education": [], "keywords": ["SQL"]}
    if "recruiter" in system:
        return {"subject": "Junior Data Analyst application", "body": "Hi Jane, I applied today. CV attached."}
    answers = {}
    for field in payload["fields"]:
        label, options = field["question"].lower(), field["options"]
        if "sponsor" in label:
            value = "No"
        elif options and "tools" in label:
            value = ["SQL", "Python"]
        elif options:
            value = "Yes"
        elif field["type"] in ("number",) or "years" in label:
            value = "2"
        elif "why" in label:
            value = "I enjoy turning data into decisions."
        else:
            value = "Immediately available"
        answers[field["key"]] = value
    return {"answers": answers}


class Fixtures(SimpleHTTPRequestHandler):
    records = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES), **kwargs)

    def do_POST(self):
        Fixtures.records.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@unittest.skipUnless(autoapply.ENGINE.exists(), "engine missing")
class BrowserEngineTests(TempStorage):
    @classmethod
    def setUpClass(cls):
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Playwright not installed")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixtures)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        super().setUp()
        Fixtures.records.clear()
        settings.update({"AUTO_SUBMIT_ENABLED": "1", "GROQ_API_KEY": "test-key", "TAILOR_CV": "1"})
        self.patches = [patch.object(ai, "ask_json", side_effect=fake_ai),
                        patch.object(autoapply, "_allowed_host", return_value=True),
                        patch.object(channels, "needs_login", return_value=False)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        super().tearDown()

    def application(self, page):
        learner = db.add_learner({"name": "Amina Example", "email": "amina@example.net", "phone": "07700900123",
                                  "location": "London", "skills": "SQL, Excel, Power BI", "right_to_work": "yes",
                                  "permission_to_apply": True, "linkedin_url": "https://www.linkedin.com/in/amina",
                                  "projects": "Sales dashboard built with SQL and Power BI."})
        company = db.upsert_company("Example Ltd", "example-ltd.co.uk", ats="manual")
        job = db.upsert_job(company, "manual", page, "Junior Data Analyst", f"{self.base}/{page}",
                            "SQL and Power BI. Contact Jane Recruiter at talent@example-ltd.co.uk.", "London")
        with db.connection() as conn:
            applyqueue.enqueue(conn, learner, db.row("SELECT * FROM jobs WHERE id=?", (job,)))
        return db.row("SELECT * FROM applications WHERE job_id=?", (job,))

    def test_multi_step_ats_form_is_completed_and_confirmed(self):
        app = self.application("ats.html")
        result = autoapply.process(app["id"], submit=True)
        self.assertEqual(result["status"], "confirmed", result)
        sent = Fixtures.records[-1]
        self.assertEqual((sent["first_name"], sent["last_name"]), ("Amina", "Example"))
        self.assertEqual(sent["email"], "amina@example.net")
        self.assertEqual(sent["phone"], "07700900123")
        self.assertEqual(sent["linkedin"], "https://www.linkedin.com/in/amina")
        self.assertEqual(sent["visa"], "n")
        self.assertEqual(sent["auth"], "yes")
        self.assertEqual(sorted(sent["tools"]), ["python", "sql"])
        self.assertEqual(sent["city"], "London, England, United Kingdom")
        self.assertEqual(sent["consent"], "on")
        self.assertEqual(sent["years_sql"], "2")
        self.assertEqual(sent["resume_name"], "Amina_Example_CV.pdf")
        self.assertGreater(sent["resume_size"], 1000)
        self.assertTrue(sent["why"])
        stored = db.row("SELECT * FROM applications WHERE id=?", (app["id"],))
        self.assertEqual(stored["status"], "confirmed")
        self.assertIn("Thank you for applying", stored["confirmation"])
        # AI answers are saved for reuse; job-specific motivation answers are not.
        saved = {r["question"] for r in db.rows("SELECT question FROM question_answers")}
        self.assertTrue(any("visa sponsorship" in q for q in saved))
        self.assertFalse(any(q.lower().startswith("why do you want") for q in saved))

    def test_tailored_cv_drops_invented_employers_and_skills(self):
        app = self.application("ats.html")
        version = tailor.cv_for_application(app)
        self.assertTrue(version["label"].startswith("Tailored"))
        from placement import cv_files
        info, binary = cv_files.read_variant(version["id"])
        text = cv_files.extract_text(info, binary)
        self.assertIn("Power BI", text)
        self.assertNotIn("Invented Corp", text)
        self.assertNotIn("Kubernetes", text)

    def test_easy_apply_dialog_steps_through_to_sent(self):
        app = self.application("easyapply.html")
        result = autoapply.process(app["id"], submit=True)
        self.assertEqual(result["status"], "confirmed", result)
        sent = Fixtures.records[-1]
        self.assertEqual(sent["phone"], "07700900123")
        self.assertEqual(sent["resume"], "Amina_Example_CV.pdf")
        self.assertEqual(sent["drive"], "Yes")
        self.assertEqual(sent["years"], "2")

    def test_dropdown_that_ignores_scripted_events_uses_trusted_input(self):
        app = self.application("trusted.html")
        result = autoapply.process(app["id"], submit=True)
        self.assertEqual(result["status"], "confirmed", result)
        self.assertEqual(Fixtures.records[-1], {"first_name": "Amina", "visa": "No"})

    def test_prepare_mode_stops_before_submit(self):
        app = self.application("ats.html")
        result = autoapply.process(app["id"], submit=False)
        self.assertEqual(result["status"], "prepared", result)
        self.assertEqual(Fixtures.records, [])

    def test_recruiter_email_found_in_advert_and_sent_with_cv(self):
        app = self.application("ats.html")
        autoapply.process(app["id"], submit=True)
        loop = db.run("""INSERT INTO loops(learner_id,name,titles,mode,created_at,auto_email)
            VALUES(?,?,?,?,?,1)""", (app["learner_id"], "Loop", '["Data Analyst"]', "auto", db.utc_now()))
        db.run("UPDATE applications SET loop_id=? WHERE id=?", (loop, app["id"]))
        found = recruiters.find_for_job(app["job_id"])
        self.assertEqual(found[0]["email"], "talent@example-ltd.co.uk")
        delivered = []
        settings.update({"SMTP_HOST": "smtp.example.org", "SMTP_USERNAME": "placements@example.org",
                         "SMTP_PASSWORD": "x", "SMTP_FROM": "placements@example.org"})
        with patch.object(outreach, "_deliver", side_effect=lambda message, config: delivered.append(message)):
            summary = outreach.process_auto()
        self.assertEqual(summary["sent"], 1, summary)
        message = delivered[0]
        self.assertEqual(message["To"], "talent@example-ltd.co.uk")
        self.assertIn("Amina Example", message["From"])
        self.assertEqual(message["Reply-To"], "amina@example.net")
        attachments = [part.get_filename() for part in message.iter_attachments()]
        self.assertEqual(attachments, ["Amina_Example_CV.pdf"])
        self.assertIn("opt out", message.get_body().get_content())


if __name__ == "__main__":
    unittest.main()
