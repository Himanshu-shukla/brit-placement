"""Find a recruiter / hiring email for a job (LoopCV-style).

Order of preference:
1. emails printed in the job advert (or returned by the board)          confidence: listed
2. role inboxes published on the company website (contacts.scan)        confidence: published
3. the hiring contact's name (advert / LinkedIn poster) + email pattern  confidence: verified | pattern
4. common recruitment inboxes on the company's mail domain              confidence: verified | guessed
Guessed addresses require the domain to have MX records; SMTP RCPT verification is used
when outbound port 25 is reachable (often blocked on home broadband, then skipped).
"""

import random
import re
import smtplib
import socket
import struct
import urllib.request

from . import channels, contacts, db, settings

EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-])")
IGNORE = re.compile(r"^(no-?reply|donotreply|privacy|gdpr|dpo|data\.?protection|abuse|postmaster|webmaster|support|sales|press|media)@", re.I)
BOARD_DOMAINS = ("linkedin.com", "indeed.com", "glassdoor", "reed.co.uk", "totaljobs.com", "cv-library.co.uk",
                 "adzuna", "jooble", "example.com", "sentry.io", "wixpress.com", "greenhouse.io", "lever.co")
ROLE_INBOXES = ("careers", "recruitment", "jobs", "talent", "hr", "recruiting", "hiring", "people")
NAME_HINT = re.compile(r"(?:contact|speak (?:to|with)|email|reach out to|send (?:your )?(?:cv|application) to|"
                       r"hiring manager[:,]?|recruiter[:,]?|questions,? (?:please )?contact)\s+([A-Z][a-z]+)\s+([A-Z][a-zA-Z'-]+)")
_mx_cache, _port25 = {}, {"ok": None}


def _dns_query(domain, server, qtype=15):
    ident = random.randint(0, 65535)
    query = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    for part in domain.strip(".").split("."):
        query += bytes([len(part)]) + part.encode()
    query += b"\x00" + struct.pack(">HH", qtype, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(4)
        sock.sendto(query, (server, 53))
        data = sock.recv(4096)
    header = struct.unpack(">HHHHHH", data[:12])
    if header[0] != ident or header[1] & 0x000F:
        return []
    offset, answers = 12, header[3]

    def read_name(position):
        labels, jumped, end = [], False, position
        while True:
            length = data[position]
            if length & 0xC0 == 0xC0:
                pointer = struct.unpack(">H", data[position:position + 2])[0] & 0x3FFF
                if not jumped:
                    end = position + 2
                position, jumped = pointer, True
                continue
            if length == 0:
                return ".".join(labels), (end if jumped else position + 1)
            labels.append(data[position + 1:position + 1 + length].decode(errors="replace"))
            position += length + 1

    _, offset = read_name(offset)
    offset += 4
    found = []
    for _ in range(answers):
        _, offset = read_name(offset)
        rtype, _, _, length = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        if rtype == 15:
            preference = struct.unpack(">H", data[offset:offset + 2])[0]
            host, _ = read_name(offset + 2)
            found.append((preference, host))
        offset += length
    return [host for _, host in sorted(found)]


def mx_hosts(domain):
    domain = domain.lower().strip(".")
    if domain not in _mx_cache:
        hosts = []
        for server in ("1.1.1.1", "8.8.8.8"):
            try:
                hosts = _dns_query(domain, server)
                break
            except OSError:
                continue
        _mx_cache[domain] = hosts
    return _mx_cache[domain]


def smtp_check(email, mx):
    """True/False when the mail server answers, None when verification is impossible."""
    if _port25["ok"] is False or not mx:
        return None
    try:
        with smtplib.SMTP(mx[0], 25, timeout=8) as server:
            _port25["ok"] = True
            server.helo("mail.britinstitute.uk")
            server.mail("verify@britinstitute.uk")
            code, _ = server.rcpt(email)
            server.rset()
            return code in (250, 251)
    except (OSError, smtplib.SMTPException):
        if _port25["ok"] is None:
            _port25["ok"] = False
        return None


def catch_all(domain, mx):
    return smtp_check(f"zz{random.randint(10**6, 10**7)}nobody@{domain}", mx) is True


def _domain_ok(domain):
    return bool(domain) and "." in domain and not any(b in domain for b in BOARD_DOMAINS)


def company_domain(job):
    company = db.row("SELECT * FROM companies WHERE id=?", (job["company_id"],)) if job.get("company_id") else None
    for url in (job.get("company_url"), company and company.get("careers_url"), job.get("apply_url"), job.get("url")):
        host = channels.host_of(url or "")
        if host and not channels.board_for(url) and not channels.is_ats(url):
            return host.removeprefix("www.").removeprefix("careers.").removeprefix("jobs.")
    if company and company.get("domain"):
        return company["domain"].removeprefix("www.")
    name = re.sub(r"\b(ltd|limited|plc|llp|group|holdings|uk|the|inc|co|company)\b", " ",
                  (job.get("company_name") or "").lower())
    slug = re.sub(r"[^a-z0-9]+", "", name)
    if len(slug) < 3:
        return ""
    for candidate in (f"{slug}.co.uk", f"{slug}.com", f"{slug}.uk", f"{slug}.org.uk"):
        if not mx_hosts(candidate):
            continue
        try:
            request = urllib.request.Request("https://" + candidate, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                page = response.read(200_000).decode("utf-8", "replace").lower()
            words = [w for w in name.split() if len(w) > 2]
            if not words or any(w in page for w in words):
                return candidate
        except OSError:
            continue
    return ""


def _save(company_id, email, source, kind, name="", confidence="listed"):
    email = email.lower().strip(" .,;:")
    db.run("""INSERT INTO contacts(company_id,email,source_url,kind,discovered_at,name,confidence)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(company_id,email) DO UPDATE SET
        name=COALESCE(NULLIF(excluded.name,''),contacts.name)""",
           (company_id, email, source[:500], kind, db.utc_now(), name[:120], confidence))
    return db.row("SELECT * FROM contacts WHERE company_id=? AND email=?", (company_id, email))


def _patterns(first, last, domain):
    first, last = re.sub(r"[^a-z]", "", first.lower()), re.sub(r"[^a-z]", "", last.lower())
    return [f"{first}.{last}@{domain}", f"{first}@{domain}", f"{first[0]}{last}@{domain}",
            f"{first}{last}@{domain}", f"{first[0]}.{last}@{domain}", f"{last}@{domain}"]


def find_for_job(job_id):
    job = db.row("""SELECT jobs.*,companies.name AS company_name FROM jobs LEFT JOIN companies
        ON companies.id=jobs.company_id WHERE jobs.id=?""", (int(job_id),))
    if not job:
        raise ValueError("Job not found")
    company_id = job["company_id"]
    suppressed = {r["email"] for r in db.rows("SELECT email FROM suppressions")}
    found = []
    # 1. Emails printed in the advert or returned by the board.
    listed = set(EMAIL.findall(" ".join([job.get("emails") or "", job.get("description") or ""])))
    for email in sorted(listed):
        domain = email.split("@", 1)[1].lower()
        if IGNORE.search(email) or not _domain_ok(domain) or email.lower() in suppressed:
            continue
        found.append(_save(company_id, email, job["url"], "listed", confidence="listed"))
    if found:
        return found
    existing = db.rows("SELECT * FROM contacts WHERE company_id=? ORDER BY id", (company_id,))
    existing = [c for c in existing if c["email"] not in suppressed]
    if existing:
        return existing
    domain = company_domain(job)
    if not domain:
        return []
    db.run("UPDATE companies SET domain=? WHERE id=? AND domain=''", (domain, company_id))
    # 2. Published role inboxes on the company website.
    company = db.row("SELECT * FROM companies WHERE id=?", (company_id,))
    if not company["careers_url"]:
        db.run("UPDATE companies SET careers_url=? WHERE id=?", (f"https://{domain}/careers", company_id))
    try:
        scan = contacts.scan(company_id, max_pages=4)
        if scan["emails"]:
            return db.rows("SELECT * FROM contacts WHERE company_id=?", (company_id,))
    except (ValueError, OSError):
        pass
    if not settings.enabled("EMAIL_GUESSING"):
        return []
    mx = mx_hosts(domain)
    if not mx:
        return []
    wildcard = catch_all(domain, mx)
    # 3. Named hiring contact.
    name = (job.get("poster_name") or "").split()
    hint = NAME_HINT.search(job.get("description") or "")
    if len(name) < 2 and hint:
        name = [hint.group(1), hint.group(2)]
    if len(name) >= 2:
        candidates = _patterns(name[0], name[-1], domain)
        chosen, confidence = candidates[0], "pattern"
        if not wildcard:
            for email in candidates:
                verdict = smtp_check(email, mx)
                if verdict is True:
                    chosen, confidence = email, "verified"
                    break
                if verdict is None:
                    break
        if chosen not in suppressed:
            return [_save(company_id, chosen, job["url"], "person", " ".join(name), confidence)]
    # 4. Recruitment inboxes on the mail domain.
    for prefix in ROLE_INBOXES:
        email = f"{prefix}@{domain}"
        if email in suppressed:
            continue
        verdict = None if wildcard else smtp_check(email, mx)
        if verdict is True:
            return [_save(company_id, email, f"mx:{domain}", "role", confidence="verified")]
        if verdict is None:
            return [_save(company_id, f"careers@{domain}", f"mx:{domain}", "role", confidence="guessed")]
    return []
