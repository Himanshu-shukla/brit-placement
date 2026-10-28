"""Collect explicitly published business contact emails from company-owned pages."""

import re
import ipaddress
import socket
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

from . import config, db

EMAIL_PATTERN = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])")
ROLE_PREFIXES = ("jobs", "careers", "recruitment", "recruiting", "talent", "hr", "people", "hiring", "info", "contact")


class PublicContactParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.mailto = set()
        self.links = set()
        self.public_text = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "a":
            href = data.get("href", "")
            if href.lower().startswith("mailto:"):
                self.mailto.add(urllib.parse.unquote(href.split(":", 1)[1].split("?", 1)[0]))
            elif href:
                self.links.add(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.public_text.append(data)


def extract(html_text):
    parser = PublicContactParser()
    parser.feed(html_text)
    emails = set(parser.mailto)
    emails.update(EMAIL_PATTERN.findall(" ".join(parser.public_text)))
    return {e.strip(" ,.;:").lower() for e in emails if EMAIL_PATTERN.fullmatch(e.strip(" ,.;:"))}, parser.links


def is_public_role_address(email):
    prefix = email.lower().split("@", 1)[0]
    return prefix in ROLE_PREFIXES or any(prefix.startswith(word + ".") or prefix.startswith(word + "-") for word in ROLE_PREFIXES)


def _same_domain(candidate, original):
    try:
        a, b = urllib.parse.urlsplit(candidate), urllib.parse.urlsplit(original)
        host_a, host_b = (a.hostname or "").lower(), (b.hostname or "").lower()
        if not (a.scheme in ("https", "http") and host_a == host_b):
            return False
        if not host_a or host_a == "localhost" or host_a.endswith(".local"):
            return False
        try:
            return ipaddress.ip_address(host_a).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def _allowed(url):
    parsed = urllib.parse.urlsplit(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(robots_url)
    try:
        robots.read()
        return robots.can_fetch(config.USER_AGENT, url)
    except OSError:
        # If robots permissions cannot be assessed, don't fetch the page.
        return False


def scan(company_id, max_pages=4):
    company = db.row("SELECT * FROM companies WHERE id=?", (int(company_id),))
    if not company:
        raise ValueError("Unknown company")
    base = company["careers_url"]
    if not base or not _same_domain(base, base):
        raise ValueError("A public HTTPS/HTTP careers URL with a real host is required")
    host = urllib.parse.urlsplit(base).hostname
    try:
        for result in socket.getaddrinfo(host, None):
            if not ipaddress.ip_address(result[4][0]).is_global:
                raise ValueError("Company host resolves to a non-public IP address")
    except socket.gaierror as exc:
        raise ValueError("Could not resolve company domain") from exc
    pending = [base]
    seen = set()
    result = {"emails": [], "contact_forms": [], "pages": [], "errors": []}
    while pending and len(seen) < max(1, min(max_pages, 6)):
        url = pending.pop(0)
        if url in seen or not _same_domain(url, base):
            continue
        seen.add(url)
        if not _allowed(url):
            result["errors"].append(f"Disallowed or unverified robots.txt: {url}")
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=12) as response:
                if not _same_domain(response.geturl(), base) or "text/html" not in response.headers.get("Content-Type", ""):
                    continue
                body = response.read(1_000_000).decode("utf-8", errors="replace")
            emails, links = extract(body)
            result["pages"].append(url)
            for address in sorted(emails):
                # Extract published role addresses only. Never guess personal emails.
                if is_public_role_address(address) and address not in result["emails"]:
                    db.run("INSERT OR IGNORE INTO contacts(company_id,email,source_url,discovered_at) VALUES(?,?,?,?)",
                           (company_id, address, url, db.utc_now()))
                    result["emails"].append(address)
            for href in links:
                absolute = urllib.parse.urljoin(url, href).split("#", 1)[0]
                if not _same_domain(absolute, base):
                    continue
                path = urllib.parse.urlsplit(absolute).path.lower()
                if any(word in path for word in ("contact", "career", "recruit", "about", "people", "join")):
                    if "contact" in path:
                        db.run("INSERT OR IGNORE INTO contact_forms(company_id,url,discovered_at) VALUES(?,?,?)",
                               (company_id, absolute, db.utc_now()))
                        if absolute not in result["contact_forms"]:
                            result["contact_forms"].append(absolute)
                    if absolute not in seen and absolute not in pending:
                        pending.append(absolute)
        except (OSError, ValueError) as exc:
            result["errors"].append(f"{url}: {str(exc)[:150]}")
    db.log_event("scan_contacts", {"company_id": company_id, **result})
    return result
