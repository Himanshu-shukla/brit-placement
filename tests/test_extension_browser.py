"""Loads the real Chrome extension into Chromium and lets its background runner apply to
local fixture pages through the real backend API (claim -> apply -> report)."""

import os
import tempfile
import threading
import unittest
from pathlib import Path

import app
from test_workflows import TempStorage
from test_engine_browser import Fixtures
from placement import applyqueue, db, questions, settings
from http.server import ThreadingHTTPServer

EXTENSION = Path(__file__).parent.parent / "extension"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).parent.parent / "data" / "playwright"))


class ExtensionBrowserTests(TempStorage):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("Playwright not installed")
        # Some sandboxed Windows sessions cannot start Chromium with any extension; probe once.
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as p:
            try:
                p.chromium.launch_persistent_context(profile, channel="chromium", headless=True, timeout=20000, args=[
                    f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"]).close()
            except Exception as exc:
                raise unittest.SkipTest(f"Chromium cannot load extensions in this environment: {str(exc)[:80]}")
        cls.fixtures_server = ThreadingHTTPServer(("127.0.0.1", 0), Fixtures)
        cls.fixture_base = f"http://127.0.0.1:{cls.fixtures_server.server_address[1]}"
        threading.Thread(target=cls.fixtures_server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.fixtures_server.shutdown()
        cls.fixtures_server.server_close()

    def setUp(self):
        super().setUp()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def queue(self, page, answers):
        Fixtures.records.clear()
        learner = db.add_learner({"name": "Amina Example", "email": "amina@example.net", "phone": "07700900123",
                                  "location": "London", "skills": "SQL, Excel", "right_to_work": "yes",
                                  "permission_to_apply": True})
        for question, answer in answers.items():
            questions.save({"learner_id": learner, "question": question, "answer": answer, "approved": True})
        company = db.upsert_company("Example Ltd", ats="manual")
        job = db.upsert_job(company, "manual", page, "Data Analyst", f"{self.fixture_base}/{page}", "SQL", "London")
        with db.connection() as conn:
            applyqueue.enqueue(conn, learner, db.row("SELECT * FROM jobs WHERE id=?", (job,)))
        settings.update({"TAILOR_CV": "0", "APPLY_DELAY_MIN_SECONDS": "0", "APPLY_DELAY_MAX_SECONDS": "0"})
        return learner, db.row("SELECT * FROM applications WHERE job_id=?", (job,))

    def run_extension(self, learner):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as p:
            # Extensions need full Chromium ("new" headless), not the headless shell.
            context = p.chromium.launch_persistent_context(profile, channel="chromium", headless=True, timeout=40000, args=[
                f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"])
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker", timeout=15000)
            worker.evaluate("""cfg => chrome.storage.local.set(cfg)""", {
                "backend": f"http://127.0.0.1:{self.port}", "token": app.admin_token(), "learnerId": str(learner),
                "companySites": True})
            result = worker.evaluate("() => runQueue(true)")
            context.close()
        return result

    def test_extension_runner_applies_easy_apply_with_saved_answers(self):
        learner, application = self.queue("easyapply.html", {
            "Do you have a valid UK driving licence?": "Yes",
            "How many years of work experience do you have with Excel?": "1"})
        result = self.run_extension(learner)
        self.assertEqual(result["status"], "confirmed", result)
        self.assertEqual(Fixtures.records[-1]["drive"], "Yes")
        self.assertEqual(Fixtures.records[-1]["years"], "1")
        self.assertTrue(Fixtures.records[-1]["resume"].endswith(".pdf"))
        stored = db.row("SELECT * FROM applications WHERE id=?", (application["id"],))
        self.assertEqual(stored["status"], "confirmed")
        self.assertEqual(db.row("SELECT engine FROM application_attempts")["engine"], "chrome_extension")

    def test_extension_uses_debugger_for_dropdowns_that_ignore_scripts(self):
        learner, application = self.queue("trusted.html", {"Would you need us to sponsor a work visa?": "No"})
        result = self.run_extension(learner)
        self.assertEqual(result["status"], "confirmed", result)
        self.assertEqual(Fixtures.records[-1], {"first_name": "Amina", "visa": "No"})


if __name__ == "__main__":
    unittest.main()
