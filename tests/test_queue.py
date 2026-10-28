"""Application queue, routing, settings, discovery parsing and extension API endpoints."""

import base64
import json
from unittest.mock import patch

import test_workflows
from test_workflows import TempStorage
from placement import applyqueue, channels, cv, cv_files, db, loops, settings, sources


class RoutingTests(TempStorage):
    def test_channels(self):
        self.assertEqual(channels.channel_for("https://www.linkedin.com/jobs/view/1/"), "extension")
        self.assertEqual(channels.channel_for("https://uk.indeed.com/viewjob?jk=abc"), "extension")
        self.assertEqual(channels.channel_for("https://www.totaljobs.com/job/123"), "extension")
        self.assertEqual(channels.channel_for("https://boards.greenhouse.io/acme/jobs/1"), "server")
        # Indeed listing with a direct employer link applies on the employer site.
        job = {"url": "https://uk.indeed.com/viewjob?jk=1", "apply_url": "https://jobs.lever.co/acme/1", "easy_apply": 0}
        self.assertEqual(channels.target(job), "https://jobs.lever.co/acme/1")
        self.assertEqual(channels.target({**job, "easy_apply": 1}), "https://uk.indeed.com/viewjob?jk=1")

    def test_loop_title_matching_is_word_based(self):
        self.assertTrue(loops.title_matches("Junior Data Analyst", "Data Analyst - Fintech"))
        self.assertTrue(loops.title_matches("BI Analyst", "Graduate BI Analyst (Hybrid)"))
        self.assertFalse(loops.title_matches("Data Analyst", "Data Engineer"))


class QueueTests(TempStorage):
    def setUp(self):
        super().setUp()
        self.learner, self.company, self.job = self.fixtures()
        try:
            pdf = cv.resume_pdf(self.learner)
        except RuntimeError:
            self.skipTest("ReportLab optional")
        cv_files.upload({"learner_id": self.learner, "filename": "master.pdf", "is_default": True,
                         "content_base64": base64.b64encode(pdf).decode()})
        settings.update({"TAILOR_CV": "0"})

    def add_job(self, n, url):
        job = db.upsert_job(self.company, "linkedin", f"li-{n}", "Junior Data Analyst", url, "SQL", "London")
        with db.connection() as conn:
            applyqueue.enqueue(conn, self.learner, db.row("SELECT * FROM jobs WHERE id=?", (job,)))
        return db.row("SELECT * FROM applications WHERE job_id=?", (job,))

    def test_claim_payload_and_daily_limit(self):
        self.add_job(1, "https://www.linkedin.com/jobs/view/1/")
        self.add_job(2, "https://www.linkedin.com/jobs/view/2/")
        settings.update({"DAILY_APPLY_LIMIT": "1"})
        payload = applyqueue.next_for_extension(self.learner)
        self.assertEqual(payload["application"]["channel"], "extension")
        self.assertEqual(payload["profile"]["first_name"], "Amina")
        self.assertTrue(base64.b64decode(payload["cv"]["content_base64"]).startswith(b"%PDF"))
        # The claimed application counts toward today's limit, so nothing else is handed out.
        self.assertIsNone(applyqueue.next_for_extension(self.learner)["application"])
        applyqueue.record(payload["application"]["id"], {"status": "submitted", "evidence": "Your application was sent"})
        self.assertIsNone(applyqueue.next_for_extension(self.learner)["application"])
        settings.update({"DAILY_APPLY_LIMIT": "5"})
        self.assertIsNotNone(applyqueue.next_for_extension(self.learner)["application"])
        self.assertEqual(db.summary()["applied_today"], 1)

    def test_results_route_retry_and_review(self):
        app = self.add_job(3, "https://www.linkedin.com/jobs/view/3/")
        applyqueue.claim(application_id=app["id"])
        applyqueue.record(app["id"], {"status": "external", "url": "https://boards.greenhouse.io/acme/jobs/9"})
        moved = db.row("SELECT * FROM applications WHERE id=?", (app["id"],))
        self.assertEqual((moved["status"], moved["channel"]), ("queued", "server"))
        applyqueue.record(app["id"], {"status": "reroute", "reason": "CAPTCHA"})
        self.assertEqual(db.row("SELECT channel FROM applications WHERE id=?", (app["id"],))["channel"], "extension")
        applyqueue.record(app["id"], {"status": "needs_input", "unknown": ["National Insurance number"]})
        stored = db.row("SELECT * FROM applications WHERE id=?", (app["id"],))
        self.assertEqual(stored["status"], "needs_review")
        self.assertIn("National Insurance", stored["notes"])
        self.assertEqual(applyqueue.retry(app["id"])["status"], "queued")

    def test_auto_loop_routes_by_url(self):
        db.upsert_job(self.company, "indeed", "in-1", "Data Analyst", "https://uk.indeed.com/viewjob?jk=1", "SQL Excel",
                      "London", apply_url="https://jobs.lever.co/acme/1")
        task = loops.create({"learner_id": self.learner, "titles": "Data Analyst", "mode": "auto"})
        loops.run(task["id"])
        rows = {r["apply_url"]: r for r in db.rows("SELECT * FROM applications")}
        self.assertEqual(rows["https://jobs.lever.co/acme/1"]["channel"], "server")
        self.assertEqual(rows["https://jobs.lever.co/acme/1"]["loop_id"], task["id"])


class SourceTests(TempStorage):
    def test_settings_mask_secrets_and_keep_unchanged_mask(self):
        settings.update({"GROQ_API_KEY": "gsk_secret_1234", "DAILY_APPLY_LIMIT": "30"})
        shown = settings.public()
        self.assertEqual(shown["GROQ_API_KEY"], "••••1234")
        settings.update({"GROQ_API_KEY": shown["GROQ_API_KEY"]})
        self.assertEqual(settings.get("GROQ_API_KEY"), "gsk_secret_1234")
        self.assertEqual(settings.number("DAILY_APPLY_LIMIT"), 30)

    def test_harvest_and_relevance(self):
        result = sources.harvest({"board": "totaljobs", "jobs": [
            {"url": "https://www.totaljobs.com/job/data-analyst/acme-job123", "title": "Data Analyst", "company": "Acme",
             "location": "Leeds", "easy_apply": True},
            {"url": "javascript:alert(1)", "title": "bad"}]})
        self.assertEqual(result["saved"], 1)
        job = db.row("SELECT * FROM jobs")
        self.assertEqual((job["source"], job["easy_apply"]), ("totaljobs", 1))
        self.assertTrue(sources.relevant("Senior Data Analyst (Remote)", "junior data analyst"))
        self.assertFalse(sources.relevant("Data Engineer", "data analyst"))

    def test_nhs_results_parser(self):
        page = '''<ul class="nhsuk-list search-results"><li class="search-result" data-test="search-result">
          <h2><a href="/candidate/jobadvert/C9-26-1?keyword=x" data-test="search-result-job-title"> Data Analyst </a></h2>
          <div data-test="search-result-location"><h3 class="b"> Leeds Teaching Hospitals <div class="location-font-size"> Leeds LS1 </div></h3></div>
          <li data-test="search-result-salary" class="marginBt">Salary: <strong>£29,000 a year</strong></li>
          <li data-test="search-result-publicationDate">Date posted: <strong>23 October 2026</strong></li></li></ul>'''
        with patch.object(sources, "get_text", return_value=page):
            jobs = list(sources.nhs("data analyst"))
        self.assertEqual(jobs[0]["url"], "https://www.jobs.nhs.uk/candidate/jobadvert/C9-26-1")
        self.assertEqual(jobs[0]["company_name"], "Leeds Teaching Hospitals")
        self.assertEqual(jobs[0]["salary"], "£29,000 a year")

    def test_search_isolates_board_failures(self):
        def broken(query, location):
            raise ValueError("board down")
            yield
        feed = lambda query, location: iter([{"source": "remotive", "source_id": "1", "title": "Data Analyst",
                                              "url": "https://remotive.com/1", "company_name": "Acme"}])
        with patch.dict(sources.SEARCHERS, {"nhs": broken, "remotive": feed}, clear=True):
            summary = sources.search(["Data Analyst"], boards=["nhs", "remotive"])
        self.assertEqual(summary["jobs_seen"], 1)
        self.assertEqual(summary["errors"][0]["source"], "nhs")


class ExtensionAPITests(test_workflows.APITests):
    def test_extension_round_trip(self):
        learner, company, _ = self.fixtures()
        settings.update({"TAILOR_CV": "0"})
        code, body = self.request("/api/extension/apply-now", "POST", {"learner_id": learner, "job": {
            "url": "https://www.linkedin.com/jobs/view/42/", "title": "Data Analyst", "company": "Acme"}})
        if code == 503 or (code == 400 and "reportlab" in json.dumps(body).lower()):
            self.skipTest("ReportLab optional")
        self.assertEqual(code, 200, body)
        app_id = body["application"]["id"]
        code, answers = self.request("/api/questions/answer", "POST", {"application_id": app_id, "fields": []})
        self.assertEqual((code, answers["unanswered"]), (200, []))
        code, saved = self.request("/api/extension/result", "POST", {"application_id": app_id, "status": "submitted",
                                                                   "evidence": "Your application was sent", "steps": 4})
        self.assertEqual(saved["status"], "confirmed")
        code, boards = self.request("/api/boards")
        self.assertIn("linkedin", [b["id"] for b in boards])
        code, shown = self.request("/api/settings")
        self.assertIn("GROQ_MODEL", shown)
        code, tasks = self.request("/api/extension/search-tasks")
        self.assertEqual((code, tasks), (200, []))
