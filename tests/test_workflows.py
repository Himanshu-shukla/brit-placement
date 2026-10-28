"""Offline and local HTTP end-to-end tests. Run: python -m unittest discover -s tests -v"""

import base64
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import app
from placement import config, contacts, cv, db, matching, outreach, sources, ai


class TempStorage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brit-placement-test-")
        self.original = config.DATA_DIR, config.DB_PATH, config.CV_DIR
        self.old_token_file = app.TOKEN_FILE
        config.DATA_DIR = Path(self.temp.name)
        config.DB_PATH = config.DATA_DIR / "test.sqlite3"
        config.CV_DIR = config.DATA_DIR / "cvs"
        app.TOKEN_FILE = config.DATA_DIR / "admin-token.txt"
        db.initialize()

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, config.CV_DIR = self.original
        app.TOKEN_FILE = self.old_token_file
        self.temp.cleanup()

    def fixtures(self):
        learner=db.add_learner({"name":"Amina Example","email":"amina@example.net",
            "skills":"SQL, Excel, Power BI", "right_to_work":"yes", "permission_to_apply":True,
            "projects":"SQL dashboard exercise."})
        company=db.upsert_company("Example Ltd", "example.org", "https://example.org/careers", "manual")
        job=db.upsert_job(company,"manual","job-1","Junior Data Analyst",
                          "https://example.org/jobs/1","Knowledge of SQL, Power BI and Excel", "London")
        return learner,company,job


class MatchingTests(TempStorage):
    def test_matching_and_duplicate_jobs(self):
        learner,company,job=self.fixtures()
        duplicate=db.upsert_job(company,"manual","job-1","Junior Data Analyst", "https://example.org/jobs/1","SQL, Power BI", "London")
        self.assertEqual(job,duplicate)
        result=matching.match_all()
        self.assertEqual(result["matches_scored"],1)
        record=db.row("SELECT * FROM matches WHERE learner_id=? AND job_id=?",(learner,job))
        self.assertEqual(record["decision"],"matched")

    def test_sponsor_import_and_eligibility(self):
        learner,company,job=self.fixtures()
        db.run("UPDATE learners SET right_to_work='no',sponsorship=1 WHERE id=?",(learner,))
        self.assertEqual(matching.import_sponsors("Organisation Name,Route\nExample Ltd,Skilled Worker\nOther Ltd,Temporary Worker\n"),2)
        candidate=db.row("SELECT * FROM learners WHERE id=?",(learner,))
        vacancy=db.row("SELECT jobs.*,companies.name AS company_name FROM jobs JOIN companies ON companies.id=jobs.company_id WHERE jobs.id=?",(job,))
        score,decision,reasons=matching.calculate(candidate,vacancy,{"example ltd"})
        self.assertEqual(decision,"matched")
        self.assertTrue(any("sponsor register" in reason for reason in reasons))
        self.assertEqual(matching.calculate(candidate,vacancy,set())[1],"review")
        vacancy["description"]+=" Sorry, no sponsorship available."
        self.assertEqual(matching.calculate(candidate,vacancy,{"example ltd"})[1],"excluded")

    def test_contacts_only_published_role_inboxes(self):
        emails,links=contacts.extract('<a href="mailto:careers@example.org">Write</a> Contact: recruitment@example.org or alice@example.org <a href="/contact">Contact</a><script>hr@fake.org</script>')
        self.assertIn("careers@example.org",emails)
        self.assertIn("recruitment@example.org",emails)
        self.assertIn("alice@example.org",emails)
        self.assertNotIn("hr@fake.org",emails)
        self.assertEqual({a for a in emails if contacts.is_public_role_address(a)},
                         {"careers@example.org","recruitment@example.org"})
        self.assertIn("/contact",links)

    def test_cv_has_no_invented_employer(self):
        learner,_,job=self.fixtures()
        html=cv.resume_html(learner,job)
        self.assertIn("Amina Example",html)
        self.assertIn("SQL dashboard exercise",html)
        self.assertNotIn("Bachelor of Science",html)

    def test_export_pdf_if_reportlab_available(self):
        try:
            import reportlab
        except ImportError:
            self.skipTest("Optional ReportLab package not installed")
        learner,_,job=self.fixtures()
        content=cv.resume_pdf(learner,job)
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertGreater(len(content),1000)

    def test_outreach_rejects_no_consent_and_suppression(self):
        learner,company,job=self.fixtures()
        contact=db.run("INSERT INTO contacts(company_id,email,source_url,discovered_at) VALUES(?,?,?,?)",
                       (company,"jobs@example.org","https://example.org/careers",db.utc_now()))
        message=outreach.draft(learner,job,contact)
        self.assertEqual(message["status"],"draft")
        with self.assertRaisesRegex(ValueError,"approval"):
            outreach.send(message["id"])
        db.run("INSERT INTO suppressions(email,reason,created_at) VALUES(?,?,?)",("jobs@example.org","opt out",db.utc_now()))
        with self.assertRaisesRegex(ValueError,"suppressed"):
            outreach.send(message["id"],True)
        with self.assertRaisesRegex(ValueError,"suppressed"):
            outreach.draft(learner,job,contact)

    def test_greenhouse_lever_ashby_adapters(self):
        def stub(url,*args,**kwargs):
            if "greenhouse" in url:return {"jobs":[{"id":1,"title":"Data Analyst","absolute_url":"https://a.example/jobs/1","location":{"name":"London"},"content":"<p>SQL</p>"}]}
            if "lever" in url:return [{"id":"abc","text":"BI Analyst","hostedUrl":"https://b.example/j","categories":{"location":"Remote"},"descriptionPlain":"Power BI"}]
            return {"jobs":[{"id":"xyz","title":"Insights Analyst","jobUrl":"https://c.example/j","location":"Manchester","descriptionPlain":"Excel"}]}
        with patch.object(sources,"get_json",side_effect=stub):
            self.assertEqual(list(sources.greenhouse("example"))[0]["description"],"SQL")
            self.assertEqual(list(sources.lever("example"))[0]["source_id"],"abc")
            self.assertEqual(list(sources.ashby("example"))[0]["location"],"Manchester")

    def test_employer_sync_persists_public_job_and_deduplicates(self):
        db.upsert_company("Company Demo",ats="greenhouse",ats_slug="company-demo")
        feed=[{"source":"greenhouse","source_id":"role-42","title":"Reporting Analyst",
               "url":"https://company.example/jobs/42","description":"SQL"}]
        with patch.dict(sources.ADAPTERS,{"greenhouse":lambda slug: iter(feed)}):
            self.assertEqual(sources.sync_companies()["jobs_seen"],1)
            self.assertEqual(sources.sync_companies()["jobs_seen"],1)
        self.assertEqual(len(db.rows("SELECT * FROM jobs")),1)

    def test_jobspy_style_csv_import(self):
        csv_body="title,company,job_url,location,description,site,date_posted\nJunior Data Analyst,Example Ltd,https://example.org/jobs/a,London,SQL,indeed,2026-10-27\n"
        self.assertEqual(sources.import_jobs_csv(csv_body)["jobs_imported"],1)
        self.assertEqual(sources.import_jobs_csv(csv_body)["jobs_imported"],1)
        jobs=db.rows("SELECT * FROM jobs")
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["source"],"csv-indeed")

    def test_contact_scanner_persists_careers_role_email(self):
        _,company,_=self.fixtures()
        class Response:
            headers={"Content-Type":"text/html"}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return "https://example.org/careers"
            def read(self,n):return b'<a href="mailto:careers@example.org">Careers</a><p>bob@example.org</p>'
        with patch.object(contacts,"_allowed",return_value=True),patch.object(contacts.socket,"getaddrinfo",return_value=[(None,None,None,None,("93.184.215.14",0))]),patch.object(contacts.urllib.request,"urlopen",return_value=Response()):
            result=contacts.scan(company,max_pages=1)
        self.assertEqual(result["emails"],["careers@example.org"])
        self.assertEqual(db.row("SELECT * FROM contacts WHERE company_id=?",(company,))["email"],"careers@example.org")

    def test_smtp_send_approval_includes_reply_tracking_id(self):
        learner,company,job=self.fixtures()
        contact=db.run("INSERT INTO contacts(company_id,email,source_url,discovered_at) VALUES(?,?,?,?)",
                       (company,"careers@example.org","https://example.org",db.utc_now()))
        message=outreach.draft(learner,job,contact)
        sent=[]
        class SMTP:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def login(self,*args):pass
            def send_message(self,mail):sent.append(mail)
        with patch.dict(os.environ,{"SMTP_HOST":"smtp.example.org","SMTP_USERNAME":"a@example.org","SMTP_PASSWORD":"not-a-real-password","SMTP_FROM":"a@example.org"}),patch.object(outreach.smtplib,"SMTP_SSL",SMTP):
            self.assertEqual(outreach.send(message["id"],approved=True)["status"],"sent")
        self.assertEqual(len(sent),1)
        self.assertIn(f"[BRIT-{message['id']}]",str(sent[0]["Subject"]))
        with self.assertRaisesRegex(ValueError,"Only existing draft"):
            outreach.send(message["id"],approved=True)

    def test_ai_reranking_bounded_and_no_eligibility_override(self):
        self.fixtures()
        matching.match_all()
        before=db.row("SELECT * FROM matches")
        with patch.dict(os.environ,{"OLLAMA_MODEL":"test-local-model"}),patch.object(ai,"ask",return_value='{"score": 0, "reason": "Portfolio could improve"}'):
            self.assertEqual(ai.refine(limit=1)["refined"],1)
        after=db.row("SELECT * FROM matches")
        self.assertEqual(after["decision"],"matched")
        self.assertEqual(after["score"],before["score"]-15)
        self.assertIn("AI suggestion",after["reasons"])


class APITests(TempStorage):
    def setUp(self):
        super().setUp()
        self.server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def request(self,path,method="GET",data=None,authorized=True):
        headers={}
        if authorized:headers["X-Placement-Key"]=app.admin_token()
        if data is not None:headers["Content-Type"]="application/json"
        req=urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
            method=method,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=4) as response:
                payload=response.read()
                return response.status,json.loads(payload) if 'application/json' in response.headers.get('Content-Type','') else payload.decode()
        except urllib.error.HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_local_http_end_to_end(self):
        code,body=self.request("/api/learners",authorized=False)
        self.assertEqual(code,401)
        code,body=self.request("/api/learners",'POST',{"name":"Amina Example","email":"amina@example.net","skills":"SQL, Power BI, Excel","right_to_work":"yes","permission_to_apply":True})
        self.assertEqual(code,201)
        learner=body["id"]
        code,body=self.request("/api/companies",'POST',{"name":"Example Ltd","ats":"manual"})
        self.assertEqual(code,201)
        company=body["id"]
        code,body=self.request("/api/jobs",'POST',{"company_id":company,"title":"Junior Data Analyst","url":"https://example.org/j/1","description":"SQL Power BI Excel"})
        self.assertEqual(code,201)
        job=body["id"]
        code,data=self.request('/api/matches/run','POST',{})
        self.assertEqual(data["matches_scored"],1)
        code,data=self.request('/api/matches')
        self.assertEqual(data[0]["decision"],"matched")
        code,data=self.request('/api/applications','POST',{"learner_id":learner,"job_id":job})
        self.assertEqual(code,201)
        application=data["id"]
        code,data=self.request('/api/applications','POST',{"learner_id":learner,"job_id":job})
        self.assertEqual(application,data["id"])
        code,data=self.request(f'/api/applications/{application}/status','POST',{"status":"confirmed"})
        self.assertEqual(code,400)
        code,data=self.request(f'/api/applications/{application}/status','POST',{"status":"confirmed","confirmation":"Employer confirmation number XYZ-123"})
        self.assertEqual(code,200)
        code,data=self.request('/api/dashboard')
        self.assertEqual(data["confirmed_applications"],1)
        code,html=self.request(f'/api/learners/{learner}/resume')
        self.assertIn("Amina Example",html)
        code,html=self.request('/')
        self.assertIn('PLACEMENT AUTOMATION',html)

    def test_consent_blocks_application(self):
        code,data=self.request('/api/learners','POST',{"name":"No Consent","email":"no@example.net"})
        learner=data['id']
        _,company=self.request('/api/companies','POST',{"name":"Example Ltd"})
        _,job=self.request('/api/jobs','POST',{"company_id":company['id'],"title":"BI Analyst","url":"https://example.org/2"})
        code,data=self.request('/api/applications','POST',{"learner_id":learner,"job_id":job['id']})
        self.assertEqual(code,400)
        self.assertIn('consent',data['error'].lower())

    def test_blank_experience_and_edit_email(self):
        code,created=self.request('/api/learners','POST',{"name":"Test Learner","email":"candidate@test.net","experience_years":"","permission_to_apply":True})
        self.assertEqual(code,201)
        _,company=self.request('/api/companies','POST',{"name":"Test Ltd"})
        _,job=self.request('/api/jobs','POST',{"company_id":company['id'],"title":"Data Analyst","url":"https://example.org/test"})
        contact=db.run("INSERT INTO contacts(company_id,email,source_url,discovered_at) VALUES(?,?,?,?)",
                       (company['id'],"careers@example.org","https://example.org",db.utc_now()))
        code,draft=self.request('/api/outreach/draft','POST',{"learner_id":created['id'],"job_id":job['id'],"contact_id":contact})
        self.assertEqual(code,201)
        code,updated=self.request(f'/api/outreach/{draft["id"]}/edit','POST',{"subject":"Verified draft subject","body":"Reviewed by staff with permission"})
        self.assertEqual(code,200)
        self.assertEqual(db.row('SELECT subject FROM outreach WHERE id=?',(draft['id'],))['subject'],'Verified draft subject')


if __name__ == '__main__':
    unittest.main()
