"""Recruiter emails: manual drafts and automatic personalised emails for auto-mode loops."""

import smtplib
import ssl
import imaplib
import re
from email import policy
from email.parser import BytesParser
from email.utils import formataddr, parseaddr
from email.message import EmailMessage

from . import ai, cv_files, db, settings


def draft(learner_id, job_id, contact_id):
    candidate = db.row("SELECT * FROM learners WHERE id=?", (int(learner_id),))
    job = db.row("SELECT jobs.*,companies.name AS company_name FROM jobs LEFT JOIN companies ON companies.id=jobs.company_id WHERE jobs.id=?", (int(job_id),))
    contact = db.row("SELECT * FROM contacts WHERE id=?", (int(contact_id),))
    if not candidate or not job or not contact:
        raise ValueError("Invalid candidate, job or contact")
    if not candidate["permission_to_apply"]:
        raise ValueError("Candidate has not granted permission to represent them")
    if contact["company_id"] != job["company_id"]:
        raise ValueError("Contact must belong to the advertised employer")
    if db.row("SELECT email FROM suppressions WHERE email=?", (contact["email"],)):
        raise ValueError("Contact opted out or is suppressed")
    subject = f"Application enquiry: {job['title']} — {candidate['name']}"
    body = (f"Hello recruitment team,\n\nI'm contacting you from Brit Institute with {candidate['name']}'s "
            f"permission about your advertised {job['title']} position.\n\n"
            f"The candidate's relevant skills include {candidate['skills'] or 'data analytics'}. "
            "We can share their verified CV and portfolio if the role is open to applications. "
            f"The vacancy is listed here: {job['url']}\n\n"
            "If you prefer that we do not email you about opportunities, reply 'opt out' and "
            "we will remove this contact from our outreach list.\n\n"
            "Kind regards,\nBrit Institute Placements\n")
    with db.connection() as conn:
        conn.execute("""INSERT INTO outreach(learner_id,job_id,contact_id,subject,body,status,created_at)
        VALUES(?,?,?,?,?,'draft',?) ON CONFLICT(learner_id,job_id,contact_id) DO NOTHING""",
        (learner_id, job_id, contact_id, subject, body, db.utc_now()))
        record = conn.execute("SELECT id FROM outreach WHERE learner_id=? AND job_id=? AND contact_id=?", (learner_id,job_id,contact_id)).fetchone()
    return db.row("SELECT * FROM outreach WHERE id=?", (record[0],))


def _smtp_config():
    config = {k: settings.get("SMTP_" + k) for k in ("HOST", "PORT", "USERNAME", "PASSWORD", "FROM", "FROM_NAME")}
    config["FROM"] = config["FROM"] or config["USERNAME"]
    if not all((config["HOST"], config["USERNAME"], config["PASSWORD"], config["FROM"])):
        raise ValueError("Set the SMTP host, username, password and From address in Settings")
    return config


def _deliver(message, config):
    port = int(config["PORT"] or 465)
    if port == 465:
        with smtplib.SMTP_SSL(config["HOST"], port, context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(config["USERNAME"], config["PASSWORD"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config["HOST"], port, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(config["USERNAME"], config["PASSWORD"])
            smtp.send_message(message)


def send(outreach_id, approved=False):
    if not approved:
        raise ValueError("This email requires explicit approval")
    record = db.row("""SELECT outreach.*,contacts.email,learners.permission_to_apply,learners.name AS learner_name,
      learners.email AS learner_email FROM outreach JOIN contacts ON contacts.id=outreach.contact_id
      JOIN learners ON learners.id=outreach.learner_id WHERE outreach.id=?""", (int(outreach_id),))
    if not record or record["status"] != "draft":
        raise ValueError("Only existing draft emails can be sent")
    if not record["permission_to_apply"]:
        raise ValueError("Learner consent is required")
    if db.row("SELECT 1 FROM suppressions WHERE email=?", (record["email"],)):
        raise ValueError("Address is suppressed")
    config = _smtp_config()
    msg = EmailMessage()
    # Automatic emails are written as the candidate; replies go straight to them.
    sender_name = record["learner_name"] if record["auto"] else config["FROM_NAME"]
    msg["From"] = formataddr((sender_name, config["FROM"]))
    msg["To"], msg["Subject"] = record["email"], record["subject"] + f" [BRIT-{outreach_id}]"
    if record["auto"] and record["learner_email"] and record["learner_email"] != config["FROM"]:
        msg["Reply-To"] = record["learner_email"]
    msg.set_content(record["body"])
    if record["cv_version_id"]:
        try:
            info, binary = cv_files.read_variant(record["cv_version_id"])
            maintype, subtype = info["mime_type"].split("/", 1)
            msg.add_attachment(binary, maintype=maintype, subtype=subtype, filename=info["filename"])
        except ValueError:
            pass
    _deliver(msg, config)
    db.run("UPDATE outreach SET status='sent',sent_at=? WHERE id=? AND status='draft'", (db.utc_now(),outreach_id))
    db.log_event("outreach_sent", {"outreach_id": outreach_id, "to": record["email"]})
    return {"id": outreach_id, "status": "sent"}


def test_smtp():
    config = _smtp_config()
    msg = EmailMessage()
    msg["From"] = formataddr((config["FROM_NAME"], config["FROM"]))
    msg["To"], msg["Subject"] = config["FROM"], "Placement OS SMTP test"
    msg.set_content("SMTP is configured correctly. Automatic recruiter emails will be sent from this address.")
    _deliver(msg, config)
    return {"ok": True, "sent_to": config["FROM"]}


DEFAULT_TEMPLATE = ("Hi {first_name},\n\nI've just applied for the {job_title} role at {company} and wanted to "
                    "introduce myself directly. My background includes {skills}, and I'd welcome the chance to "
                    "discuss how I could contribute to your team.\n\nI've attached my CV for reference.\n\n"
                    "Kind regards,\n{candidate_name}\n{candidate_phone}")
OPT_OUT = "\n\nIf you'd prefer not to receive emails like this, just reply 'opt out' and I won't contact you again."


def compose(learner, job, contact, template=""):
    """Personalised recruiter email from the candidate (Groq when available, template otherwise)."""
    first = contact["name"].split()[0] if contact.get("name") else "there"
    fields = {"first_name": first, "job_title": job["title"], "company": job.get("company_name") or "your company",
              "skills": learner.get("skills") or "data analysis", "candidate_name": learner["name"],
              "candidate_phone": learner.get("phone") or "", "candidate_email": learner["email"]}
    base = template or DEFAULT_TEMPLATE
    try:
        body = base.format(**fields)
    except (KeyError, IndexError, ValueError):
        body = base
    subject = f"{job['title']} application — {learner['name']}"
    if ai.available():
        try:
            result = ai.ask_json(
                "Write a short, warm, professional email from a UK job candidate to a recruiter after applying. "
                "90-150 words, first person, specific to the job, mention the CV is attached. Use only the "
                "candidate's real facts. No placeholders, no opt-out line. Keep the style of style_reference. "
                'Return {"subject": str, "body": str}.',
                {"candidate": {"name": learner["name"], "skills": learner.get("skills"), "summary": learner.get("summary"),
                               "projects": str(learner.get("projects") or "")[:1500], "phone": learner.get("phone")},
                 "recipient_first_name": "" if first == "there" else first,
                 "job": {"title": job["title"], "company": job.get("company_name"),
                         "description": str(job.get("description") or "")[:3000]},
                 "style_reference": body}, max_tokens=700, temperature=0.6)
            if result.get("body") and len(result["body"]) < 4000:
                body, subject = str(result["body"]).strip(), str(result.get("subject") or subject)[:180]
        except ValueError as exc:
            db.log_event("compose_error", {"error": str(exc)[:200]})
    return subject, body.strip() + OPT_OUT


def sent_today(learner_id):
    return db.row("SELECT COUNT(*) AS n FROM outreach WHERE learner_id=? AND status IN ('sent','replied') AND sent_at>=?",
                  (int(learner_id), db.utc_now()[:10]))["n"]


def process_auto(limit=10):
    """For auto-mode loops: find a recruiter email for each new application and send it."""
    from . import recruiters
    if not settings.enabled("AUTO_EMAIL_ENABLED"):
        return {"drafted": 0, "sent": 0, "reason": "Automatic emails are turned off in Settings"}
    try:
        _smtp_config()
        can_send = True
    except ValueError:
        can_send = False
    pending = db.rows("""SELECT a.id AS application_id,a.learner_id,a.job_id,a.cv_version_id,lp.email_template
        FROM applications a JOIN loops lp ON lp.id=a.loop_id JOIN learners l ON l.id=a.learner_id
        WHERE lp.mode='auto' AND lp.auto_email=1 AND l.permission_to_apply=1
          AND a.status IN ('confirmed','queued','applying','needs_review','prepared')
          AND NOT EXISTS (SELECT 1 FROM outreach o WHERE o.learner_id=a.learner_id AND o.job_id=a.job_id)
          AND NOT EXISTS (SELECT 1 FROM events e WHERE e.kind='recruiter_none' AND e.detail=CAST(a.id AS TEXT))
        ORDER BY CASE a.status WHEN 'confirmed' THEN 0 ELSE 1 END, a.id LIMIT 60""")
    summary = {"drafted": 0, "sent": 0, "no_contact": 0, "errors": [], "smtp_configured": can_send}
    daily = settings.number("DAILY_EMAIL_LIMIT", 25)
    for item in pending:
        if summary["drafted"] >= limit:
            break
        if sent_today(item["learner_id"]) >= daily:
            continue
        try:
            found = recruiters.find_for_job(item["job_id"])
        except (ValueError, OSError) as exc:
            summary["errors"].append(str(exc)[:200])
            found = []
        if not found:
            db.run("INSERT INTO events(kind,detail,created_at) VALUES('recruiter_none',?,?)",
                   (str(item["application_id"]), db.utc_now()))
            summary["no_contact"] += 1
            continue
        contact = found[0]
        if db.row("SELECT 1 FROM suppressions WHERE email=?", (contact["email"],)):
            continue
        learner = db.row("SELECT * FROM learners WHERE id=?", (item["learner_id"],))
        job = db.row("""SELECT jobs.*,companies.name AS company_name FROM jobs LEFT JOIN companies
            ON companies.id=jobs.company_id WHERE jobs.id=?""", (item["job_id"],))
        subject, body = compose(learner, job, contact, item["email_template"])
        with db.connection() as conn:
            conn.execute("""INSERT INTO outreach(learner_id,job_id,contact_id,subject,body,status,created_at,auto,cv_version_id)
                VALUES(?,?,?,?,?,'draft',?,1,?) ON CONFLICT(learner_id,job_id,contact_id) DO NOTHING""",
                (learner["id"], job["id"], contact["id"], subject, body, db.utc_now(), item["cv_version_id"]))
            outreach_id = conn.execute("SELECT id FROM outreach WHERE learner_id=? AND job_id=? AND contact_id=?",
                                       (learner["id"], job["id"], contact["id"])).fetchone()[0]
        summary["drafted"] += 1
        if can_send:
            try:
                send(outreach_id, approved=True)
                summary["sent"] += 1
            except (ValueError, OSError, smtplib.SMTPException) as exc:
                summary["errors"].append(str(exc)[:200])
    db.log_event("auto_outreach", summary)
    return summary


def poll_replies(days=30):
    """Label replies in the mailbox (read-only IMAP) and honour opt-outs."""
    host,username,password = settings.get("IMAP_HOST"),settings.get("IMAP_USERNAME"),settings.get("IMAP_PASSWORD")
    if not all((host,username,password)):
        raise ValueError("Set the IMAP host, username and password in Settings")
    from datetime import datetime, timedelta, timezone
    since=(datetime.now(timezone.utc)-timedelta(days=days)).strftime("%d-%b-%Y")
    tracked=0
    with imaplib.IMAP4_SSL(host, int(settings.get("IMAP_PORT") or 993)) as imap:
        imap.login(username,password)
        status,_=imap.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Cannot open inbox")
        status, result=imap.search(None, "SINCE", since)
        if status != "OK":
            raise ValueError("Inbox search failed")
        for mail_id in result[0].split()[-200:]:
            code,data=imap.fetch(mail_id,"(BODY.PEEK[])")
            if code != "OK":
                continue
            blob=next((part[1] for part in data if isinstance(part,tuple)),None)
            if not blob:
                continue
            email=BytesParser(policy=policy.default).parsebytes(blob)
            match=re.search(r"\[BRIT-(\d+)\]",str(email.get("Subject", "")))
            if not match:
                continue
            identifier=int(match.group(1))
            record=db.row("""SELECT o.*,c.email FROM outreach o JOIN contacts c ON c.id=o.contact_id WHERE o.id=? AND o.status='sent'""",(identifier,))
            if not record:
                continue
            sender=parseaddr(str(email.get("From", "")))[1].lower()
            if record["email"].lower() != sender:
                continue
            section=email.get_body(preferencelist=("plain",))
            body=section.get_content() if section else ""
            body=str(body)[:1500]
            db.run("UPDATE outreach SET status='replied',response=? WHERE id=?",(body,identifier))
            if re.search(r"\b(opt[ -]?out|unsubscribe|do not contact|stop emailing)\b",body.lower()):
                db.run("INSERT OR REPLACE INTO suppressions(email,reason,created_at) VALUES(?,?,?)",
                       (record["email"],"Recipient requested no further contact",db.utc_now()))
            tracked+=1
    db.log_event("poll_replies", {"tracked":tracked})
    return {"replies_tracked":tracked}
