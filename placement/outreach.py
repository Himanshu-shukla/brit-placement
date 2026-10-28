"""Candidate-approved, individually reviewed recruitment outreach."""

import os
import smtplib
import ssl
import imaplib
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from email.message import EmailMessage

from . import db


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


def send(outreach_id, approved=False):
    if not approved:
        raise ValueError("This email requires explicit staff approval")
    record = db.row("""SELECT outreach.*,contacts.email,learners.permission_to_apply
      FROM outreach JOIN contacts ON contacts.id=outreach.contact_id
      JOIN learners ON learners.id=outreach.learner_id WHERE outreach.id=?""", (int(outreach_id),))
    if not record or record["status"] != "draft":
        raise ValueError("Only existing draft emails can be sent")
    if not record["permission_to_apply"]:
        raise ValueError("Learner consent is required")
    if db.row("SELECT 1 FROM suppressions WHERE email=?", (record["email"],)):
        raise ValueError("Address is suppressed")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or username
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    if not all((username, password, sender, host)):
        raise ValueError("SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM must be configured")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, record["email"], record["subject"] + f" [BRIT-{outreach_id}]"
    msg.set_content(record["body"])
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
        smtp.login(username, password)
        smtp.send_message(msg)
    db.run("UPDATE outreach SET status='sent',sent_at=? WHERE id=? AND status='draft'", (db.utc_now(),outreach_id))
    db.log_event("outreach_sent", {"outreach_id": outreach_id, "to": record["email"]})
    return {"id": outreach_id, "status": "sent"}


def poll_replies(days=30):
    """Optionally label real replies in mailbox. Safe read-only access via IMAP."""
    host,username,password = os.getenv("IMAP_HOST"),os.getenv("IMAP_USERNAME"),os.getenv("IMAP_PASSWORD")
    if not all((host,username,password)):
        raise ValueError("IMAP_HOST, IMAP_USERNAME, and IMAP_PASSWORD are required")
    from datetime import datetime, timedelta, timezone
    since=(datetime.now(timezone.utc)-timedelta(days=days)).strftime("%d-%b-%Y")
    tracked=0
    with imaplib.IMAP4_SSL(host, int(os.getenv("IMAP_PORT", "993"))) as imap:
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
