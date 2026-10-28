"""Honest, explainable matching: eligibility before keyword score."""

import csv
import io
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from . import db

SKILL_TERMS = {
    "sql": ("sql", "postgresql", "mysql", "t-sql"),
    "python": ("python", "pandas"),
    "excel": ("excel", "spreadsheets"),
    "power bi": ("power bi", "powerbi", "dax"),
    "tableau": ("tableau",),
    "statistics": ("statistics", "statistical"),
    "azure": ("azure",),
    "aws": ("aws", "amazon web services"),
    "etl": ("etl", "data pipelines"),
}
BLOCK_WORDS = ("senior", "lead", "principal", "head of", "director", "staff ", "manager")
ROLE_WORDS = ("data analyst", "business analyst", "bi analyst", "reporting analyst",
              "insights analyst", "analytics", "power bi", "data technician", "junior data", "graduate data")
EXCLUDE_WORDS = ("data engineer", "machine learning engineer", "senior data scientist")


def normalize(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).lower())).strip()


def skills_in(value):
    text = " " + normalize(value) + " "
    return {skill for skill, terms in SKILL_TERMS.items() if any(" " + normalize(term) + " " in text for term in terms)}


def import_sponsors(csv_body):
    content = csv_body.decode("utf-8-sig") if isinstance(csv_body, bytes) else str(csv_body)
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    company_col = next((h for h in headers if "organisation" in h.lower() or "organization" in h.lower() or "sponsor name" in h.lower()), None)
    route_col = next((h for h in headers if "route" in h.lower()), None)
    if not company_col:
        raise ValueError("CSV must contain an Organisation Name or sponsor name column")
    saved = 0
    with db.connection() as conn:
        conn.execute("DELETE FROM sponsors")
        for line in reader:
            company = (line.get(company_col) or "").strip()
            if company:
                conn.execute("INSERT OR REPLACE INTO sponsors(name,normalized_name,route) VALUES(?,?,?)",
                             (company, normalize(company), (line.get(route_col) or "") if route_col else ""))
                saved += 1
    db.log_event("sponsor_import", {"count": saved})
    return saved


class _SponsorLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if ".csv" in href.lower() or "format=csv" in href.lower():
                self.urls.append(href)


def sync_official_sponsors():
    """Discover the currently published CSV from the official GOV.UK register."""
    origin = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
    request = urllib.request.Request(origin, headers={"User-Agent": "BritInstitutePlacementBot/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read(2_000_000).decode("utf-8", errors="replace")
    parser = _SponsorLinks()
    parser.feed(page)
    candidates = [urllib.parse.urljoin(origin, ref) for ref in parser.urls]
    # Only government publishing infrastructure; not arbitrary redirect links.
    candidates = [u for u in candidates if urllib.parse.urlsplit(u).hostname in
                  ("assets.publishing.service.gov.uk", "www.gov.uk")]
    if not candidates:
        raise ValueError("Official sponsor CSV was not linked; download it manually from GOV.UK and import")
    request = urllib.request.Request(candidates[0], headers={"User-Agent": "BritInstitutePlacementBot/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname not in ("assets.publishing.service.gov.uk", "www.gov.uk"):
            raise ValueError("Sponsor CSV redirected outside GOV.UK")
        data = response.read(10_000_001)
    if len(data) > 10_000_000:
        raise ValueError("Sponsor register exceeds configured size limit")
    return {"count": import_sponsors(data), "source": candidates[0]}


def company_sponsors(company_name, normalized_sponsors):
    company = normalize(company_name)
    # Only exact normalized match: ambiguous corporate affiliates must be manually verified.
    return company in normalized_sponsors


def calculate(learner, job, sponsor_names=None, loop_titles=False, allow_senior=False):
    """loop_titles=True when a search loop already matched the title to the learner's target roles."""
    title, description = normalize(job["title"]), normalize(job.get("description") or "")
    company_name = job.get("company_name") or ""
    reasons = []
    if not allow_senior and any(term in title for term in BLOCK_WORDS):
        return 0, "excluded", ["Senior or management position"]
    if not loop_titles and any(term in title for term in EXCLUDE_WORDS):
        return 0, "excluded", ["Role outside entry-level analytics"]
    if not loop_titles and not any(term in title for term in ROLE_WORDS):
        return 0, "excluded", ["Job title not within the analytics role list"]
    eligibility = str(learner.get("right_to_work", "unknown")).strip().lower()
    if eligibility == "no" and not learner.get("sponsorship"):
        return 0, "excluded", ["No confirmed UK right to work or sponsorship route"]
    if learner.get("sponsorship"):
        if company_sponsors(company_name, sponsor_names or set()):
            reasons.append("Employer appears on UK sponsor register; this vacancy's eligibility still needs confirmation")
        else:
            return 0, "review", ["Sponsorship needed; employer not exactly verified on sponsor register"]
    if any(term in description for term in ("no sponsorship", "cannot sponsor", "not able to sponsor", "right to work without sponsorship")) and learner.get("sponsorship"):
        return 0, "excluded", ["Advert explicitly states no sponsorship"]
    present = skills_in(learner.get("skills", ""))
    required = skills_in(description + " " + title)
    common = sorted(present & required)
    missing = sorted(required - present)
    own = [normalize(x) for x in str(learner.get("skills") or "").split(",") if len(normalize(x)) > 1]
    advert = " " + description + " " + title + " "
    own_hits = [x for x in own if " " + x + " " in advert]
    if required:
        score = int(45 + 50 * len(common) / len(required))
    elif loop_titles:
        # Title already matches the learner's target roles; reward their own skills found in the advert.
        score = min(92, 62 + 6 * len(own_hits))
        if own_hits:
            reasons.append("Advert mentions: " + ", ".join(own_hits[:8]))
    else:
        score = 58
    if "junior" in title or "graduate" in title or "entry level" in title:
        score += 5
    if common:
        reasons.append("Matched: " + ", ".join(common))
    if missing:
        reasons.append("Not demonstrated: " + ", ".join(missing))
    if not required and not loop_titles:
        reasons.append("No explicit skills extracted; manual review advised")
    score = min(score, 100)
    return score, "matched" if score >= 60 else "review", reasons


def match_all():
    learners = db.rows("SELECT * FROM learners WHERE active=1")
    jobs = db.rows("SELECT jobs.*,companies.name AS company_name FROM jobs LEFT JOIN companies ON companies.id=jobs.company_id WHERE jobs.active=1")
    sponsor_names = {row["normalized_name"] for row in db.rows("SELECT normalized_name FROM sponsors")}
    count = 0
    with db.connection() as conn:
        for learner in learners:
            for job in jobs:
                score, decision, reasons = calculate(learner, job, sponsor_names)
                conn.execute("""INSERT INTO matches(learner_id,job_id,score,decision,reasons,matched_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(learner_id,job_id) DO UPDATE SET
                score=excluded.score,decision=excluded.decision,reasons=excluded.reasons,matched_at=excluded.matched_at""",
                (learner["id"],job["id"],score,decision,"; ".join(reasons),db.utc_now()))
                count += 1
    db.log_event("match_all", {"matches_scored": count})
    return {"matches_scored": count, "learners": len(learners), "jobs": len(jobs)}
