"""End-to-end workflow tests for recurring loops, CVs and screening answers."""

import base64
import io
import os
import unittest
from unittest.mock import patch

from test_workflows import TempStorage
import test_workflows
from placement import autoapply, cv, cv_files, db, loops, questions


class LoopTests(TempStorage):
    def test_review_loop_filters_and_does_not_submit(self):
        learner,_,job=self.fixtures()
        task=loops.create({"learner_id":learner,"titles":"Junior Data Analyst, Power BI Analyst",
                           "location":"United Kingdom","mode":"review"})
        first=loops.run(task["id"])
        self.assertEqual(first["matched"],1)
        self.assertEqual(first["queued"],0)
        self.assertEqual(len(db.rows("SELECT * FROM loop_jobs")),1)
        self.assertEqual(len(db.rows("SELECT * FROM applications")),0)
        second=loops.run(task["id"])
        self.assertEqual(second["matched"],1)
        self.assertEqual(len(db.rows("SELECT * FROM loop_jobs")),1)
        self.assertEqual(loops.tick(discover=False)["due"],0)

    def test_auto_loop_queues_at_most_once(self):
        learner,_,job=self.fixtures()
        task=loops.create({"learner_id":learner,"titles":["Junior Data Analyst"],"mode":"auto"})
        self.assertEqual(loops.run(task["id"])["queued"],1)
        self.assertEqual(loops.run(task["id"])["queued"],0)
        self.assertEqual(len(db.rows("SELECT * FROM applications")),1)
        self.assertEqual(db.row("SELECT status FROM applications")["status"],"queued")
        self.assertEqual(db.row("SELECT mode FROM loops WHERE id=?",(task["id"],))["mode"],"auto")
        with patch.dict(os.environ,{"AUTO_SUBMIT_ENABLED":"0"}):
            self.assertEqual(autoapply.process_queue()["processed"],0)

    def test_sponsorship_review_not_auto_queued(self):
        learner,_,job=self.fixtures()
        db.run("UPDATE learners SET sponsorship=1,right_to_work='no' WHERE id=?",(learner,))
        task=loops.create({"learner_id":learner,"titles":"Data Analyst","mode":"auto"})
        self.assertEqual(loops.run(task["id"])["review"],1)
        self.assertEqual(db.rows("SELECT * FROM applications"),[])

    def test_loop_settings_updates(self):
        learner,_,_=self.fixtures()
        task=loops.create({"learner_id":learner,"titles":"Data Analyst","boards":["greenhouse","lever"]})
        updated=loops.update(task["id"],{"active":False,"mode":"auto","min_score":80})
        self.assertEqual(updated["active"],0)
        self.assertEqual(updated["mode"],"auto")
        self.assertIn("lever",updated["boards"])
        with self.assertRaisesRegex(ValueError,"interval"):
            loops.create({"learner_id":learner,"titles":"Data Analyst","frequency_minutes":15})

    def test_candidate_answer_requires_approval(self):
        learner,_,_=self.fixtures()
        value=questions.save({"learner_id":learner,"question":"Have you used SQL?", "answer":"Yes, in coursework."})
        self.assertEqual(value["approved"],0)
        self.assertEqual(questions.lookup(learner,"Have you used SQL?")["answer"],"Yes, in coursework.")
        saved=questions.save({"learner_id":learner,"question":"Have you used SQL?", "answer":"Yes, in portfolio work.","approved":True})
        self.assertEqual(saved["approved"],1)
        self.assertEqual(len(db.rows("SELECT * FROM question_answers")),1)

    def test_cv_upload_version_and_real_pdf_scoring(self):
        learner,_,job=self.fixtures()
        try:
            pdf=cv.resume_pdf(learner,job)
        except RuntimeError:
            self.skipTest("ReportLab optional")
        result=cv_files.upload({"learner_id":learner,"filename":"master.pdf","label":"Analytics CV",
                                "content_base64":base64.b64encode(pdf).decode(),"is_default":True})
        info,data=cv_files.read_variant(result["id"],learner)
        self.assertEqual(data,pdf)
        self.assertEqual(db.row("SELECT cv_path FROM learners WHERE id=?",(learner,))["cv_path"],info["path"])
        try:
            analysis=cv_files.check(info["id"],job)
        except ValueError as exc:
            if "pip install pypdf" in str(exc):self.skipTest("Optional pypdf missing")
            raise
        self.assertGreaterEqual(analysis["score"],25)
        self.assertIn("warnings",analysis)

    def test_employer_allowlist_and_linkedin_block(self):
        with patch.dict(os.environ,{"AUTO_APPLY_ALLOWED_HOSTS":"apply.example.org,www.linkedin.com"}):
            self.assertTrue(autoapply._allowed_host("https://apply.example.org/jobs/123"))
            self.assertFalse(autoapply._allowed_host("https://www.linkedin.com/jobs/view/1"))
            self.assertFalse(autoapply._allowed_host("http://apply.example.org/jobs/123"))

    def test_unattended_queue_does_not_retry_failed_application(self):
        learner,_,_=self.fixtures()
        task=loops.create({"learner_id":learner,"titles":"Junior Data Analyst","mode":"auto"})
        loops.run(task["id"])
        with patch.dict(os.environ,{"AUTO_SUBMIT_ENABLED":"1","AUTO_APPLY_ALLOWED_HOSTS":"example.org"}), \
             patch.object(autoapply,"process",side_effect=RuntimeError("Missing candidate CV")):
            first=autoapply.process_queue()
            self.assertEqual(first["processed"],1)
            self.assertEqual(first["results"][0]["status"],"error")
            self.assertEqual(autoapply.process_queue()["processed"],0)

    def test_loop_selected_cv_precedes_default(self):
        learner,_,job=self.fixtures()
        try:
            binary=cv.resume_pdf(learner,job)
        except RuntimeError:
            self.skipTest("ReportLab optional")
        default=cv_files.upload({"learner_id":learner,"filename":"master.pdf","is_default":True,
                                 "content_base64":base64.b64encode(binary).decode()})
        selected=cv_files.upload({"learner_id":learner,"filename":"powerbi.pdf","is_default":False,
                                  "content_base64":base64.b64encode(binary).decode()})
        loop=loops.create({"learner_id":learner,"titles":"Junior Data Analyst","mode":"auto",
                           "cv_version_id":selected["id"]})
        loops.run(loop["id"])
        with patch.dict(os.environ,{"AUTO_APPLY_ALLOWED_HOSTS":"example.org"}):
            item=autoapply._record(db.row("SELECT id FROM applications")["id"])
        self.assertEqual(autoapply._preferred_cv(item),db.row("SELECT path FROM cv_versions WHERE id=?",(selected["id"],))["path"])
        self.assertNotEqual(autoapply._preferred_cv(item),db.row("SELECT path FROM cv_versions WHERE id=?",(default["id"],))["path"])


class LoopAPITests(test_workflows.APITests):
    def test_loop_api_and_attempt_recording(self):
        learner,company,job=self.fixtures()
        code,new=self.request("/api/loops","POST",{"learner_id":learner,"titles":"Junior Data Analyst","mode":"auto"})
        self.assertEqual(code,201)
        code,run=self.request(f"/api/loops/{new['id']}/run","POST",{"discover":False})
        self.assertEqual(code,200)
        self.assertEqual(run["queued"],1)
        _,applications=self.request("/api/applications")
        application_id=applications[0]["id"]
        code,invalid=self.request("/api/attempts","POST",{"application_id":application_id,"status":"confirmed"})
        self.assertEqual(code,400)
        code,valid=self.request("/api/attempts","POST",{"application_id":application_id,"status":"needs_review","reason":"Visa question unanswered"})
        self.assertEqual(code,201)
        _,attempts=self.request("/api/attempts")
        self.assertEqual(attempts[0]["reason"],"Visa question unanswered")
        code,analytics=self.request("/api/analytics")
        self.assertEqual(code,200)
        self.assertEqual(analytics["by_learner"][0]["applications"],1)
        code,scheduler=self.request("/api/scheduler/status")
        self.assertEqual(code,200)
        self.assertEqual(len(scheduler["due"]),1)
        code,asset=self.request("/advanced.js",authorized=False)
        self.assertEqual(code,200)
        self.assertIn("BritAdvanced",asset)


if __name__=="__main__":
    unittest.main()
