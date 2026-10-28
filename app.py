"""Brit Institute Placement OS — run: python app.py serve"""

import argparse
import base64
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from placement import (config, db, sources, contacts, matching, outreach, cv, ai, loops, cv_files, questions,
                       autoapply, settings, applyqueue, recruiters, tailor)

STATIC = config.ROOT / "static"
TOKEN_FILE = config.DATA_DIR / "admin-token.txt"


def admin_token():
    config.bootstrap_directories()
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


class APIError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class Handler(BaseHTTPRequestHandler):
    server_version = "BritPlacement/1.0"

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") or origin in (
            f"http://{config.HOST}:{config.PORT}", f"http://localhost:{config.PORT}"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _response(self, status=200, data=None, content_type="application/json; charset=utf-8", raw=None, download_filename=None):
        blob = raw if raw is not None else json.dumps(data if data is not None else {}, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'")
        if download_filename:
            self.send_header("Content-Disposition", f'attachment; filename="{download_filename}"')
        self._cors()
        self.end_headers()
        self.wfile.write(blob)

    def _json(self, status, data):
        self._response(status, data)

    def _require_auth(self):
        sent = self.headers.get("X-Placement-Key", "")
        if not sent or not secrets.compare_digest(sent, admin_token()):
            raise APIError(401, "Valid placement admin key required")

    def _read(self, limit=3_000_000):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "Invalid content length")
        if size <= 0 or size > limit:
            raise APIError(413, "Body missing or too large")
        content = self.rfile.read(size)
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise APIError(400, "Valid JSON body required")
        if not isinstance(value, dict):
            raise APIError(400, "JSON object required")
        return value

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Placement-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            self._get()
        except APIError as exc:
            self._json(exc.code, {"error": exc.message})
        except (ValueError, sqlite3.IntegrityError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            db.log_event("server_error", {"message": str(exc)[:200]})
            self._json(500, {"error": "Internal error; check event log"})

    def _get(self):
        url = urlparse(self.path)
        path = url.path
        if path.startswith("/api/"):
            self._require_auth()
            if path == "/api/dashboard":
                return self._json(200, db.summary())
            queries = {
                "/api/learners": "SELECT * FROM learners ORDER BY id DESC",
                "/api/companies": "SELECT * FROM companies ORDER BY id DESC",
                "/api/jobs": "SELECT jobs.*,companies.name AS company_name FROM jobs LEFT JOIN companies ON companies.id=jobs.company_id ORDER BY jobs.found_at DESC,jobs.id DESC LIMIT 500",
                "/api/contacts": "SELECT contacts.*,companies.name AS company_name FROM contacts JOIN companies ON companies.id=contacts.company_id ORDER BY contacts.id DESC",
                "/api/matches": """SELECT matches.*, learners.name AS learner_name,jobs.title AS job_title,jobs.url AS url, companies.name AS company_name
                         FROM matches JOIN learners ON learners.id=matches.learner_id
                         JOIN jobs ON jobs.id=matches.job_id LEFT JOIN companies ON companies.id=jobs.company_id
                         ORDER BY matches.score DESC LIMIT 500""",
                "/api/applications": """SELECT applications.*,learners.name AS learner_name,jobs.title AS job_title,jobs.url AS url,
                         jobs.source AS source,companies.name AS company_name
                         FROM applications JOIN learners ON learners.id=applications.learner_id
                         JOIN jobs ON jobs.id=applications.job_id LEFT JOIN companies ON companies.id=jobs.company_id
                         ORDER BY applications.updated_at DESC LIMIT 500""",
                "/api/outreach": """SELECT outreach.*,contacts.email AS contact_email,learners.name AS learner_name,
                         jobs.title AS job_title FROM outreach JOIN contacts ON contacts.id=outreach.contact_id
                         JOIN learners ON learners.id=outreach.learner_id JOIN jobs ON jobs.id=outreach.job_id ORDER BY outreach.id DESC LIMIT 500""",
                "/api/events": "SELECT * FROM events ORDER BY id DESC LIMIT 100",
                "/api/contact-forms": "SELECT * FROM contact_forms ORDER BY id DESC",
                "/api/loops": """SELECT loops.*,learners.name AS learner_name,
                  (SELECT COUNT(*) FROM loop_jobs WHERE loop_id=loops.id) AS job_count
                  FROM loops JOIN learners ON learners.id=loops.learner_id ORDER BY loops.id DESC""",
                "/api/cvs": "SELECT id,learner_id,label,filename,mime_type,is_default,created_at FROM cv_versions ORDER BY id DESC",
                "/api/questions": "SELECT * FROM question_answers ORDER BY occurrence_count DESC,id DESC LIMIT 500",
                "/api/attempts": """SELECT application_attempts.*,learners.name AS learner_name,jobs.title AS job_title
                  FROM application_attempts JOIN applications ON applications.id=application_attempts.application_id
                  JOIN learners ON learners.id=applications.learner_id JOIN jobs ON jobs.id=applications.job_id
                  ORDER BY application_attempts.id DESC LIMIT 500""",
            }
            if path in queries:
                return self._json(200, db.rows(queries[path]))
            if path == "/api/loop-jobs":
                loop_id=int(self._query().get("loop_id",[0])[0])
                sql="""SELECT lj.*,j.title,j.location,j.description,j.url,j.source,j.posted_at,
                  c.name AS company_name FROM loop_jobs lj JOIN jobs j ON j.id=lj.job_id
                  LEFT JOIN companies c ON c.id=j.company_id WHERE lj.loop_id=? ORDER BY lj.score DESC,lj.id DESC LIMIT 500"""
                return self._json(200,db.rows(sql,(loop_id,)))
            if path == "/api/analytics":
                by_status=db.rows("SELECT status,COUNT(*) AS count FROM applications GROUP BY status")
                by_source=db.rows("""SELECT jobs.source AS source,COUNT(*) AS applications,
                  SUM(CASE WHEN applications.status IN ('interview','offer','hired') THEN 1 ELSE 0 END) AS interviews
                  FROM applications JOIN jobs ON jobs.id=applications.job_id GROUP BY jobs.source ORDER BY applications DESC""")
                by_learner=db.rows("""SELECT learners.id,learners.name,
                   COUNT(applications.id) AS applications,
                   SUM(CASE WHEN applications.status IN ('interview','offer','hired') THEN 1 ELSE 0 END) AS interviews,
                   SUM(CASE WHEN applications.status IN ('offer','hired') THEN 1 ELSE 0 END) AS offers
                   FROM learners LEFT JOIN applications ON applications.learner_id=learners.id GROUP BY learners.id""")
                return self._json(200,{"by_status":by_status,"by_source":by_source,"by_learner":by_learner,
                                       "summary":db.summary()})
            if path == "/api/settings":
                return self._json(200, settings.public())
            if path == "/api/boards":
                return self._json(200, [{"id": k, "label": v[0], "kind": v[1], "note": v[2]} for k, v in sources.BOARDS.items()])
            if path == "/api/extension/search-tasks":
                return self._json(200, sources.search_tasks())
            if path == "/api/scheduler/status":
                return self._json(200,{"enabled":os.getenv("PLACEMENT_SCHEDULER","1")!="0",
                  "due":db.rows("SELECT id,name,last_run_at,next_run_at,last_error,active FROM loops ORDER BY id")})
            parts = path.strip("/").split("/")
            if len(parts)==4 and parts[:2]==["api","cvs"] and parts[3]=="download":
                info,binary=cv_files.read_variant(int(parts[2]))
                return self._response(200,content_type=info["mime_type"],raw=binary,
                                      download_filename="candidate-cv-"+str(info["id"])+(".pdf" if info["mime_type"]=="application/pdf" else ".docx"))
            if len(parts) == 4 and parts[:2] == ["api", "learners"] and parts[3] == "profile":
                learner = db.row("SELECT * FROM learners WHERE id=?", (int(parts[2]),))
                if not learner:
                    raise APIError(404, "Learner not found")
                return self._json(200, learner)
            if len(parts) == 4 and parts[:2] == ["api", "learners"] and parts[3] == "resume":
                job = self._query().get("job_id", [None])[0]
                body = cv.resume_html(int(parts[2]), int(job) if job else None).encode()
                return self._response(200, content_type="text/html; charset=utf-8", raw=body)
            if len(parts) == 4 and parts[:2] == ["api", "learners"] and parts[3] == "resume.pdf":
                job = self._query().get("job_id", [None])[0]
                try:
                    body = cv.resume_pdf(int(parts[2]), int(job) if job else None)
                except RuntimeError as exc:
                    raise APIError(503, str(exc)) from exc
                return self._response(200, content_type="application/pdf", raw=body,
                                      download_filename=f"brit-candidate-{int(parts[2])}.pdf")
            raise APIError(404, "Unknown API route")
        route = "index.html" if path in ("/", "") else path.lstrip("/")
        # Only whitelisted static assets; never serve database or source files.
        allowed = {"index.html", "app.js", "styles.css", "advanced.js", "advanced.css"}
        if route not in allowed:
            raise APIError(404, "Not found")
        item = STATIC / route
        if not item.exists():
            raise APIError(404, "Asset unavailable")
        self._response(200, raw=item.read_bytes(), content_type=mimetypes.guess_type(str(item))[0] or "text/plain")

    def do_POST(self):
        try:
            self._require_auth()
            path = urlparse(self.path).path
            self._post(path, self._read(12_000_000 if path in ("/api/sponsors/import", "/api/jobs/import-csv", "/api/cvs/upload") else 3_000_000))
        except APIError as exc:
            self._json(exc.code, {"error": exc.message})
        except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            db.log_event("server_error", {"message": str(exc)[:200]})
            self._json(500, {"error": "Internal error; check event log"})

    def _post(self, path, data):
        if path == "/api/settings":
            return self._json(200, settings.update(data))
        if path == "/api/settings/test-ai":
            return self._json(200, ai.test_connection())
        if path == "/api/settings/test-smtp":
            return self._json(200, outreach.test_smtp())
        if path == "/api/questions/answer":
            return self._json(200, applyqueue.answer(data))
        if path == "/api/extension/next":
            return self._json(200, applyqueue.next_for_extension(int(data["learner_id"]), data.get("include_server", True) is not False))
        if path == "/api/extension/result":
            return self._json(200, applyqueue.record(int(data["application_id"]), data))
        if path == "/api/extension/apply-now":
            return self._json(200, applyqueue.manual(int(data["learner_id"]), data.get("job") or {}))
        if path == "/api/jobs/harvest":
            return self._json(200, sources.harvest(data))
        if path == "/api/discover":
            titles = data.get("queries") or [data.get("query") or "data analyst"]
            return self._json(200, sources.search(titles if isinstance(titles, list) else [str(titles)],
                                                  str(data.get("location") or "United Kingdom"), data.get("boards") or []))
        if path == "/api/loops/run-all":
            return self._json(200, loops.run_all())
        if path == "/api/recruiters/find":
            return self._json(200, recruiters.find_for_job(int(data["job_id"])))
        if path == "/api/outreach/auto-run":
            return self._json(200, outreach.process_auto(int(data.get("limit", 10))))
        if path == "/api/cvs/tailor":
            version = tailor.for_job(int(data["learner_id"]), int(data["job_id"]), force=data.get("force") is True)
            return self._json(200, {k: version[k] for k in ("id", "learner_id", "label", "filename", "mime_type", "created_at")})
        if path.startswith("/api/learners/") and path.endswith("/update"):
            return self._json(200, db.update_learner(int(path.split("/")[3]), data))
        if path.startswith("/api/applications/") and path.endswith("/retry"):
            return self._json(200, applyqueue.retry(int(path.split("/")[3])))
        if path.startswith("/api/applications/") and path.endswith("/apply-server"):
            return self._json(200, autoapply.process(int(path.split("/")[3]), submit=True))
        if path == "/api/loops":
            return self._json(201,loops.create(data))
        if path.startswith("/api/loops/"):
            parts=path.strip("/").split("/")
            if len(parts)!=4:
                raise APIError(404,"Unknown loop action")
            loop_id=int(parts[2])
            if parts[3]=="run":return self._json(200,loops.run(loop_id,discover=bool(data.get("discover",False))))
            if parts[3]=="update":return self._json(200,loops.update(loop_id,data))
            if parts[3]=="toggle":return self._json(200,loops.update(loop_id,{"active":bool(data.get("active"))}))
        if path == "/api/scheduler/tick":
            return self._json(200,loops.tick(discover=bool(data.get("discover",False))))
        if path == "/api/loop-jobs/review":
            record=db.row("SELECT * FROM loop_jobs WHERE loop_id=? AND job_id=?",
                          (int(data["loop_id"]),int(data["job_id"])))
            if not record:raise ValueError("Loop job not found")
            status=str(data.get("status") or "")
            if status not in ("matched","review","favorite","dismissed","queued","applied"):
                raise ValueError("Invalid loop job status")
            db.run("UPDATE loop_jobs SET status=?,updated_at=? WHERE id=?",(status,db.utc_now(),record["id"]))
            return self._json(200,{"id":record["id"],"status":status})
        if path == "/api/cvs/upload":
            return self._json(201,cv_files.upload(data))
        if path == "/api/cvs/generate":
            return self._json(201,cv_files.generate(int(data["learner_id"]),data.get("label","Generated CV"),data.get("job_id")))
        if path == "/api/cvs/check":
            return self._json(200,cv_files.check(int(data["cv_id"]),data.get("job_id")))
        if path == "/api/questions":
            return self._json(201,questions.save(data))
        if path == "/api/questions/suggest":
            return self._json(200,questions.suggest(data))
        if path == "/api/questions/lookup":
            answer=questions.lookup(int(data["learner_id"]),data["question"])
            return self._json(200,answer or {"found":False})
        if path == "/api/autoapply/run":
            return self._json(200,autoapply.process(int(data["application_id"]),
                  loop_id=int(data["loop_id"]) if data.get("loop_id") else None,submit=data.get("approved_submit") is True))
        if path == "/api/autoapply/process-queue":
            return self._json(200,autoapply.process_queue(int(data.get("limit",4))))
        if path == "/api/attempts":
            application_id=int(data["application_id"])
            if not db.row("SELECT id FROM applications WHERE id=?",(application_id,)):
                raise ValueError("Application not found")
            status=str(data.get("status") or "")
            if status not in ("prepared","needs_review","blocked","confirmed","unverified","error"):
                raise ValueError("Invalid attempt status")
            evidence=str(data.get("evidence") or "")[:900]
            if status=="confirmed" and not evidence:
                raise ValueError("Submitted confirmation evidence is required")
            attempt_id=db.run("""INSERT INTO application_attempts(application_id,loop_id,engine,status,steps,reason,evidence,created_at)
              VALUES(?,?,?,?,?,?,?,?)""",
              (application_id,int(data["loop_id"]) if data.get("loop_id") else None,
               "chrome_extension",status,min(max(int(data.get("steps") or 0),0),100),
               str(data.get("reason") or "")[:900],evidence,db.utc_now()))
            if status=="confirmed":
                db.run("UPDATE applications SET status='confirmed',confirmation=?,updated_at=? WHERE id=?",
                       (evidence,db.utc_now(),application_id))
            elif status in ("prepared","unverified","needs_review"):
                db.run("UPDATE applications SET status='prepared',notes=?,updated_at=? WHERE id=? AND status='queued'",
                       (str(data.get("reason") or "")[:900],db.utc_now(),application_id))
            return self._json(201,{"id":attempt_id,"status":status})
        if path == "/api/learners":
            return self._json(201, {"id": db.add_learner(data)})
        if path == "/api/companies":
            ats = str(data.get("ats", "manual")).lower()
            if ats not in (*sources.ADAPTERS, "manual"):
                raise ValueError("Unsupported ATS")
            return self._json(201, {"id": db.upsert_company(str(data.get("name", "")),str(data.get("domain", "")),
                str(data.get("careers_url", "")),ats,str(data.get("ats_slug", "")))})
        if path == "/api/jobs":
            jid = db.upsert_job(int(data["company_id"]),"manual",str(data.get("source_id") or data["url"]),
                str(data["title"]),str(data["url"]),str(data.get("description", "")),str(data.get("location", "")))
            return self._json(201, {"id": jid})
        if path == "/api/jobs/import-csv":
            content=str(data.get("csv", ""))
            if len(content)>10_000_000:
                raise ValueError("Job CSV exceeds 10 MB")
            return self._json(200, sources.import_jobs_csv(content))
        if path == "/api/sync":
            mode = data.get("mode", "companies")
            if mode not in ("companies", "aggregators", "all"):
                raise ValueError("Invalid sync mode")
            outcome = {}
            if mode in ("companies", "all"):
                outcome["companies"] = sources.sync_companies()
            if mode in ("aggregators", "all"):
                outcome["aggregators"] = sources.sync_aggregators(str(data.get("query", "data analyst")))
            return self._json(200, outcome)
        if path == "/api/contacts/scan":
            return self._json(200, contacts.scan(int(data["company_id"])))
        if path == "/api/matches/run":
            return self._json(200, matching.match_all())
        if path == "/api/matches/ai-refine":
            return self._json(200, ai.refine(int(data.get("limit",10))))
        if path == "/api/sponsors/import":
            value = data.get("csv", "")
            if len(value) > 10_000_000:
                raise ValueError("CSV too large")
            return self._json(200, {"count": matching.import_sponsors(value)})
        if path == "/api/sponsors/sync":
            return self._json(200, matching.sync_official_sponsors())
        if path == "/api/outreach/poll":
            return self._json(200, outreach.poll_replies())
        if path == "/api/applications":
            learner_id, job_id = int(data["learner_id"]), int(data["job_id"])
            candidate = db.row("SELECT permission_to_apply FROM learners WHERE id=?", (learner_id,))
            if not candidate or not candidate["permission_to_apply"]:
                raise ValueError("Learner must consent before adding application")
            if not db.row("SELECT 1 FROM jobs WHERE id=?", (job_id,)):
                raise ValueError("Job not found")
            with db.connection() as conn:
                conn.execute("""INSERT INTO applications(learner_id,job_id,method,updated_at)
                 VALUES(?,?,?,?) ON CONFLICT(learner_id,job_id) DO NOTHING""",
                 (learner_id,job_id,str(data.get("method", "browser_assisted")),db.utc_now()))
                rec = conn.execute("SELECT id FROM applications WHERE learner_id=? AND job_id=?", (learner_id,job_id)).fetchone()
            return self._json(201, {"id": rec[0]})
        if path.startswith("/api/applications/") and path.endswith("/status"):
            application_id = int(path.split("/")[3])
            status = str(data.get("status", "")).lower()
            if status not in ("queued", "prepared", "needs_review", "confirmed", "interview", "offer", "hired", "rejected", "withdrawn"):
                raise ValueError("Invalid status")
            current = db.row("SELECT * FROM applications WHERE id=?", (application_id,))
            if not current:
                raise ValueError("Unknown application")
            confirmation = str(data.get("confirmation", current["confirmation"]))[:500]
            if status in ("confirmed", "interview", "offer", "hired") and not confirmation:
                raise ValueError("Submission or hiring evidence/reference required")
            db.run("UPDATE applications SET status=?,confirmation=?,notes=?,updated_at=? WHERE id=?",
                   (status,confirmation,str(data.get("notes", ""))[:1000],db.utc_now(),application_id))
            db.log_event("application_status", {"application_id":application_id,"status":status})
            return self._json(200, {"id": application_id, "status": status})
        if path == "/api/outreach/draft":
            return self._json(201, outreach.draft(int(data["learner_id"]),int(data["job_id"]),int(data["contact_id"])))
        if path.startswith("/api/outreach/") and path.endswith("/edit"):
            email_id = int(path.split("/")[3])
            message = db.row("SELECT * FROM outreach WHERE id=?", (email_id,))
            if not message or message["status"] != "draft":
                raise ValueError("Only draft emails can be edited")
            subject = str(data.get("subject", "")).strip()
            body = str(data.get("body", "")).strip()
            if not subject or not body or len(subject)>200 or len(body)>10000:
                raise ValueError("Subject/body missing or too long")
            db.run("UPDATE outreach SET subject=?,body=? WHERE id=?", (subject,body,email_id))
            return self._json(200, {"id":email_id,"status":"draft"})
        if path.startswith("/api/outreach/") and path.endswith("/tailor"):
            return self._json(200, ai.tailor_email(int(path.split("/")[3])))
        if path.startswith("/api/outreach/") and path.endswith("/send"):
            return self._json(200, outreach.send(int(path.split("/")[3]), data.get("approved") is True))
        if path == "/api/suppressions":
            email = str(data.get("email", "")).strip().lower()
            if "@" not in email:
                raise ValueError("Valid email required")
            db.run("INSERT OR REPLACE INTO suppressions(email,reason,created_at) VALUES(?,?,?)",
                   (email,str(data.get("reason", "opt out")),db.utc_now()))
            return self._json(201, {"email": email, "suppressed": True})
        raise APIError(404, "Unknown API route")


def seed_demo():
    company_id = db.upsert_company("Example Analytics Ltd", "example.com", "https://example.com/careers", "manual")
    job_id = db.upsert_job(company_id, "manual", "example-job-1", "Junior Data Analyst", "https://example.com/jobs/junior-analyst",
                           "We need SQL, Power BI and Excel reporting skills.", "London")
    try:
        learner_id = db.add_learner({"name":"Demo Learner", "email":"demo-learner@example.com",
            "location":"London", "skills":"SQL, Excel, Power BI", "summary":"Analytics learner with SQL and Power BI portfolio projects",
            "projects":"Created SQL reporting queries and Power BI dashboard using sample data.", "right_to_work":"yes", "permission_to_apply":True})
    except sqlite3.IntegrityError:
        learner_id = db.row("SELECT id FROM learners WHERE email='demo-learner@example.com'")["id"]
    print(json.dumps({"company_id":company_id,"job_id":job_id,"learner_id":learner_id},indent=2))


def main():
    parser = argparse.ArgumentParser(description="Brit Institute Placement OS")
    parser.add_argument("command", choices=["serve", "sync", "match", "sync-sponsors", "poll-replies", "seed-demo", "token", "run-loops", "autoapply-queue", "outreach"])
    args = parser.parse_args()
    db.initialize()
    token = admin_token()
    if args.command == "token":
        print(token)
    elif args.command == "seed-demo":
        seed_demo()
    elif args.command == "sync":
        print(json.dumps({"employers":sources.sync_companies(),"aggregators":sources.sync_aggregators()},indent=2))
    elif args.command == "outreach":
        print(json.dumps(outreach.process_auto(),indent=2))
    elif args.command == "match":
        print(json.dumps(matching.match_all(),indent=2))
    elif args.command == "sync-sponsors":
        print(json.dumps(matching.sync_official_sponsors(),indent=2))
    elif args.command == "poll-replies":
        print(json.dumps(outreach.poll_replies(),indent=2))
    elif args.command == "run-loops":
        print(json.dumps(loops.tick(),indent=2))
    elif args.command == "autoapply-queue":
        print(json.dumps(autoapply.process_queue(),indent=2))
    else:
        print(f"\nBrit Institute Placement OS: http://{config.HOST}:{config.PORT}")
        print(f"Admin key: {token}\nOnly available on this computer by default.\n")
        server = ThreadingHTTPServer((config.HOST,config.PORT), Handler)
        stop_scheduler=threading.Event()
        def recurring():
            # Initial tick is deliberately delayed; avoids network calls at login.
            while not stop_scheduler.wait(120):
                if os.getenv("PLACEMENT_SCHEDULER","1")=="0":continue
                for name,task in (("loops",lambda:loops.tick(discover=True)),("apply",autoapply.process_queue),
                                  ("email",outreach.process_auto)):
                    try:
                        task()
                    except Exception as exc:
                        db.log_event("scheduler_error",{"task":name,"error":str(exc)[:300]})
        scheduler=threading.Thread(target=recurring,daemon=True,name="placement-scheduler")
        scheduler.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down")
        finally:
            stop_scheduler.set()
            server.server_close()


if __name__ == "__main__":
    main()
