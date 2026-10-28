"""Job discovery across job boards, public ATS feeds, licensed APIs and browser harvesting.

Three kinds of source:
* keyword search (LinkedIn, Indeed, Glassdoor via JobSpy; NHS Jobs, remote boards, Reed/Adzuna/Jooble APIs)
* per-employer ATS feeds (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Workday)
* boards that block servers (Totaljobs, CV-Library, Reed site, Guardian...) are searched by the Chrome
  extension in the learner's real browser and posted back through harvest().
"""

import base64
import csv
import html
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from xml.etree import ElementTree

from . import channels, db, config, settings

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"

# name -> (label, kind, note). kind: search | ats | browser
BOARDS = {
    "linkedin": ("LinkedIn", "search", "JobSpy search + extension Easy Apply"),
    "indeed": ("Indeed UK", "search", "JobSpy search + extension Indeed Apply"),
    "glassdoor": ("Glassdoor UK", "search", "JobSpy search + extension apply"),
    "nhs": ("NHS Jobs", "search", "Public search results"),
    "reed": ("Reed", "search", "API key (Settings) or extension search"),
    "adzuna": ("Adzuna", "search", "API key (Settings)"),
    "jooble": ("Jooble", "search", "API key (Settings)"),
    "remotive": ("Remotive", "search", "Remote roles"),
    "arbeitnow": ("Arbeitnow", "search", "Remote/EU roles"),
    "themuse": ("The Muse", "search", "London roles"),
    "himalayas": ("Himalayas", "search", "Remote roles open to UK"),
    "jobicy": ("Jobicy", "search", "Remote roles open to UK"),
    "remoteok": ("RemoteOK", "search", "Remote roles"),
    "totaljobs": ("Totaljobs", "browser", "Searched by the Chrome extension"),
    "cwjobs": ("CWJobs", "browser", "Searched by the Chrome extension"),
    "jobsite": ("Jobsite", "browser", "Searched by the Chrome extension"),
    "cv-library": ("CV-Library", "browser", "Searched by the Chrome extension"),
    "guardian": ("Guardian Jobs", "browser", "Searched by the Chrome extension"),
    "civilservice": ("Civil Service Jobs", "browser", "Harvest from an open results page"),
    "greenhouse": ("Greenhouse", "ats", "Per company board slug"),
    "lever": ("Lever", "ats", "Per company slug"),
    "ashby": ("Ashby", "ats", "Per company slug"),
    "workable": ("Workable", "ats", "Per company slug"),
    "smartrecruiters": ("SmartRecruiters", "ats", "Per company identifier"),
    "recruitee": ("Recruitee", "ats", "Per company subdomain"),
    "personio": ("Personio", "ats", "Per company subdomain"),
    "workday": ("Workday", "ats", "tenant|wd3|site"),
}

# Search pages the extension opens in the background (logged-in browser).
BROWSER_SEARCH = {
    "linkedin": "https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}&f_TPR=r259200&f_AL=true",
    "indeed": "https://uk.indeed.com/jobs?q={q}&l={loc}&fromage=3",
    "totaljobs": "https://www.totaljobs.com/jobs/{slug}{in_loc}?postedWithin=3",
    "cwjobs": "https://www.cwjobs.co.uk/jobs/{slug}{in_loc}?postedWithin=3",
    "jobsite": "https://www.jobsite.co.uk/jobs/{slug}{in_loc}?postedWithin=3",
    "cv-library": "https://www.cv-library.co.uk/{slug}-jobs{in_loc}?posted=3",
    "reed": "https://www.reed.co.uk/jobs/{slug}-jobs{in_loc}?dateCreatedOffSet=lastthreedays",
    "guardian": "https://jobs.theguardian.com/jobs/?keywords={q}&location={loc}",
}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []

    def handle_data(self, data):
        self.chunks.append(data)


def plain(value):
    parser = TextParser()
    parser.feed(html.unescape(str(value or "")))
    return " ".join(" ".join(parser.chunks).split())[:20000]


def get_json(url, headers=None, timeout=15, data=None):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": config.USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if int(response.headers.get("Content-Length", 0)) > 8_000_000:
            raise ValueError("Feed too large")
        return json.loads(response.read(8_000_001))


def get_text(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,*/*",
                                                   "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(5_000_000).decode("utf-8", errors="replace")


# ---------------------------------------------------------------- per-employer ATS feeds

def greenhouse(slug):
    data = get_json("https://boards-api.greenhouse.io/v1/boards/" + urllib.parse.quote(slug, safe="") + "/jobs?content=true")
    for entry in data.get("jobs", []):
        yield dict(source="greenhouse", source_id=str(entry["id"]), title=entry.get("title", ""),
                   url=entry.get("absolute_url", ""), location=(entry.get("location") or {}).get("name", ""),
                   description=plain(entry.get("content", "")), posted_at=entry.get("updated_at", ""))


def lever(slug):
    data = get_json("https://api.lever.co/v0/postings/" + urllib.parse.quote(slug, safe="") + "?mode=json")
    for entry in data:
        categories = entry.get("categories", {}) or {}
        description = " ".join(str(item.get("text", "")) for item in entry.get("lists", []) if isinstance(item, dict))
        yield dict(source="lever", source_id=str(entry["id"]), title=entry.get("text", ""),
                   url=entry.get("hostedUrl", ""), location=categories.get("location", ""),
                   description=plain(entry.get("descriptionPlain", "") or entry.get("description", "") or description),
                   apply_url=entry.get("applyUrl", ""))


def ashby(slug):
    data = get_json("https://api.ashbyhq.com/posting-api/job-board/" + urllib.parse.quote(slug, safe=""))
    for entry in data.get("jobs", []):
        yield dict(source="ashby", source_id=str(entry.get("id") or entry.get("jobUrl") or entry["title"]),
                   title=entry.get("title", ""), url=entry.get("jobUrl", ""),
                   location=entry.get("location", ""),
                   description=plain(entry.get("descriptionPlain", "") or entry.get("descriptionHtml", "")),
                   posted_at=entry.get("publishedAt", ""), apply_url=entry.get("applyUrl", ""))


def workable(slug):
    data = get_json("https://www.workable.com/api/accounts/" + urllib.parse.quote(slug, safe="") + "?details=true")
    for entry in data.get("jobs", []):
        yield dict(source="workable", source_id=str(entry.get("shortcode") or entry.get("id")),
                   title=entry.get("title", ""), url=entry.get("url", ""),
                   location=(entry.get("location") or {}).get("city", "") if isinstance(entry.get("location"), dict) else str(entry.get("location", "")),
                   description=plain(entry.get("description", "")), apply_url=entry.get("application_url", ""))


def smartrecruiters(slug):
    data = get_json("https://api.smartrecruiters.com/v1/companies/" + urllib.parse.quote(slug, safe="") + "/postings?limit=100")
    for entry in data.get("content", []):
        location = entry.get("location") or {}
        yield dict(source="smartrecruiters", source_id=str(entry["id"]), title=entry.get("name", ""),
                   url=f"https://jobs.smartrecruiters.com/{slug}/{entry['id']}",
                   location=", ".join(x for x in (location.get("city"), location.get("country")) if x),
                   description="", posted_at=entry.get("releasedDate", ""))


def recruitee(slug):
    data = get_json(f"https://{urllib.parse.quote(slug, safe='')}.recruitee.com/api/offers/")
    for entry in data.get("offers", []):
        yield dict(source="recruitee", source_id=str(entry["id"]), title=entry.get("title", ""),
                   url=entry.get("careers_url", ""), location=entry.get("location", ""),
                   description=plain(entry.get("description", "")), posted_at=entry.get("published_at", ""),
                   apply_url=entry.get("careers_apply_url", ""))


def personio(slug):
    request = urllib.request.Request(f"https://{urllib.parse.quote(slug, safe='')}.jobs.personio.de/xml",
                                     headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        root = ElementTree.fromstring(response.read(8_000_000))
    for position in root.iter("position"):
        identifier = position.findtext("id") or ""
        description = " ".join(plain(node.findtext("value") or "") for node in position.iter("jobDescription"))
        yield dict(source="personio", source_id=identifier, title=position.findtext("name") or "",
                   url=f"https://{slug}.jobs.personio.de/job/{identifier}", location=position.findtext("office") or "",
                   description=description, posted_at=position.findtext("createdAt") or "")


def workday(slug):
    """slug format: tenant|wd3|site   e.g. acme|wd3|External"""
    tenant, instance, site = (slug.split("|") + ["", ""])[:3]
    if not (tenant and instance and site):
        raise ValueError("Workday slug must be tenant|wdN|site")
    base = f"https://{tenant}.{instance}.myworkdayjobs.com"
    data = get_json(f"{base}/wday/cxs/{tenant}/{site}/jobs", headers={"Content-Type": "application/json"},
                    data=json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}).encode())
    for entry in data.get("jobPostings", []):
        path = entry.get("externalPath", "")
        yield dict(source="workday", source_id=f"{tenant}:{path}", title=entry.get("title", ""),
                   url=f"{base}/en-US/{site}{path}", location=entry.get("locationsText", ""),
                   description="", posted_at=entry.get("postedOn", ""))


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
            "smartrecruiters": smartrecruiters, "recruitee": recruitee, "personio": personio, "workday": workday}


# ---------------------------------------------------------------- keyword search sources

STOP = {"junior", "graduate", "senior", "entry", "level", "trainee", "apprentice", "lead", "the", "and", "uk",
        "remote", "hybrid", "part", "time", "full"}


def _core_terms(query):
    words = re.findall(r"[a-z0-9+#]+", str(query).lower())
    return [w for w in words if w not in STOP and len(w) > 1]


def relevant(title, query):
    terms = _core_terms(query)
    words = set(re.findall(r"[a-z0-9+#]+", str(title).lower()))
    return bool(terms) and all(term in words for term in terms)


def _uk_ok(location):
    text = str(location or "").lower()
    return (not text or any(x in text for x in ("uk", "united kingdom", "england", "scotland", "wales", "london",
                                                 "europe", "emea", "worldwide", "anywhere", "global", "gmt")))


def reed(query="data analyst", location="United Kingdom", pages=2):
    key = settings.get("REED_API_KEY")
    if not key:
        return
    auth = base64.b64encode((key + ":").encode()).decode()
    for page in range(max(1, min(int(pages), 5))):
        params = urllib.parse.urlencode({"keywords": query, "locationName": location, "resultsToTake": 100, "resultsToSkip": page * 100})
        data = get_json("https://www.reed.co.uk/api/1.0/search?" + params, {"Authorization": "Basic " + auth})
        for entry in data.get("results", []):
            yield dict(source="reed", source_id=str(entry["jobId"]), title=entry.get("jobTitle", ""),
                       url=entry.get("jobUrl", "") or "https://www.reed.co.uk/jobs/" + str(entry["jobId"]),
                       location=entry.get("locationName", ""),
                       description=plain(entry.get("jobDescription", "")), posted_at=entry.get("date", ""),
                       company_name=entry.get("employerName", "Unknown employer"))


def adzuna(query="data analyst", location="uk", pages=2):
    app_id, key = settings.get("ADZUNA_APP_ID"), settings.get("ADZUNA_APP_KEY")
    if not app_id or not key:
        return
    for page in range(1, max(1, min(int(pages), 5)) + 1):
        params = urllib.parse.urlencode({"app_id": app_id, "app_key": key, "what": query, "results_per_page": 50})
        data = get_json(f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}?{params}")
        for entry in data.get("results", []):
            yield dict(source="adzuna", source_id=str(entry["id"]), title=entry.get("title", ""),
                       url=entry.get("redirect_url", ""),
                       location=(entry.get("location") or {}).get("display_name", ""),
                       description=plain(entry.get("description", "")), posted_at=entry.get("created", ""),
                       company_name=(entry.get("company") or {}).get("display_name", "Unknown employer"))


def jooble(query, location="United Kingdom"):
    key = settings.get("JOOBLE_API_KEY")
    if not key:
        return
    data = get_json("https://uk.jooble.org/api/" + urllib.parse.quote(key, safe=""),
                    headers={"Content-Type": "application/json"},
                    data=json.dumps({"keywords": query, "location": location}).encode())
    for entry in data.get("jobs", []):
        yield dict(source="jooble", source_id=str(entry.get("id") or entry.get("link")), title=entry.get("title", ""),
                   url=entry.get("link", ""), location=entry.get("location", ""),
                   description=plain(entry.get("snippet", "")), posted_at=entry.get("updated", ""),
                   company_name=entry.get("company") or "Unknown employer")


def remotive(query, location=""):
    data = get_json("https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode({"search": query, "limit": 100}))
    for entry in data.get("jobs", []):
        if not _uk_ok(entry.get("candidate_required_location")):
            continue
        yield dict(source="remotive", source_id=str(entry["id"]), title=entry.get("title", ""), url=entry.get("url", ""),
                   location="Remote · " + str(entry.get("candidate_required_location") or ""),
                   description=plain(entry.get("description", "")), posted_at=entry.get("publication_date", ""),
                   company_name=entry.get("company_name") or "Unknown employer", salary=entry.get("salary", ""))


def arbeitnow(query, location=""):
    data = get_json("https://www.arbeitnow.com/api/job-board-api")
    for entry in data.get("data", []):
        if not entry.get("remote") and not _uk_ok(entry.get("location")):
            continue
        yield dict(source="arbeitnow", source_id=entry["slug"], title=entry.get("title", ""), url=entry.get("url", ""),
                   location=("Remote · " if entry.get("remote") else "") + str(entry.get("location") or ""),
                   description=plain(entry.get("description", "")), posted_at=str(entry.get("created_at", "")),
                   company_name=entry.get("company_name") or "Unknown employer")


def themuse(query, location=""):
    for place in ("London, United Kingdom", "Flexible / Remote"):
        for page in range(3):
            data = get_json("https://www.themuse.com/api/public/jobs?" + urllib.parse.urlencode(
                {"page": page, "location": place, "descending": "true"}))
            for entry in data.get("results", []):
                locations = ", ".join(x.get("name", "") for x in entry.get("locations", []))
                yield dict(source="themuse", source_id=str(entry["id"]), title=entry.get("name", ""),
                           url=(entry.get("refs") or {}).get("landing_page", ""), location=locations,
                           description=plain(entry.get("contents", "")), posted_at=entry.get("publication_date", ""),
                           company_name=(entry.get("company") or {}).get("name") or "Unknown employer")


def himalayas(query, location=""):
    data = get_json("https://himalayas.app/jobs/api/search?" + urllib.parse.urlencode({"q": query, "country": "GB"}))
    for entry in data.get("jobs", []):
        yield dict(source="himalayas", source_id=entry.get("guid") or entry.get("applicationLink"),
                   title=entry.get("title", ""), url=entry.get("applicationLink") or entry.get("guid"),
                   location="Remote · " + ", ".join(entry.get("locationRestrictions") or ["Worldwide"]),
                   description=plain(entry.get("description", "")), posted_at=str(entry.get("pubDate", "")),
                   company_name=entry.get("companyName") or "Unknown employer")


def jobicy(query, location=""):
    tag = " ".join(_core_terms(query)) or query
    data = get_json("https://jobicy.com/api/v2/remote-jobs?" + urllib.parse.urlencode({"count": 50, "geo": "uk", "tag": tag}))
    for entry in data.get("jobs", []):
        salary = f"{entry.get('salaryMin', '')}-{entry.get('salaryMax', '')} {entry.get('salaryCurrency', '')}".strip(" -")
        yield dict(source="jobicy", source_id=str(entry["id"]), title=html.unescape(entry.get("jobTitle", "")),
                   url=entry.get("url", ""), location="Remote · " + str(entry.get("jobGeo") or "UK"),
                   description=plain(entry.get("jobDescription", "")), posted_at=entry.get("pubDate", ""),
                   company_name=entry.get("companyName") or "Unknown employer", salary=salary)


def remoteok(query, location=""):
    data = get_json("https://remoteok.com/api", headers={"User-Agent": BROWSER_UA})
    for entry in data[1:] if isinstance(data, list) else []:
        if not isinstance(entry, dict) or not _uk_ok(entry.get("location")):
            continue
        yield dict(source="remoteok", source_id=str(entry.get("id")), title=entry.get("position", ""),
                   url=entry.get("url", ""), location="Remote · " + str(entry.get("location") or "Worldwide"),
                   description=plain(entry.get("description", "")), posted_at=entry.get("date", ""),
                   company_name=entry.get("company") or "Unknown employer", apply_url=entry.get("apply_url", ""))


_NHS_BLOCK = re.compile(r'data-test="search-result"(.*?)(?=data-test="search-result"\s*>|</ul>)', re.S)


def nhs(query, location=""):
    params = {"keyword": query, "language": "en"}
    if location and location.lower() not in ("uk", "united kingdom", "any", "remote"):
        params["location"] = location
    page = get_text("https://www.jobs.nhs.uk/candidate/search/results?" + urllib.parse.urlencode(params))
    for block in _NHS_BLOCK.findall(page):
        link = re.search(r'<a href="(/candidate/jobadvert/([^"?]+)[^"]*)"[^>]*data-test="search-result-job-title"[^>]*>\s*(.*?)\s*</a>', block, re.S)
        if not link:
            continue
        employer = re.search(r'data-test="search-result-location">\s*<h3[^>]*>\s*(.*?)\s*<div', block, re.S)
        place = re.search(r'class="location-font-size">\s*(.*?)\s*</div>', block, re.S)
        salary = re.search(r'data-test="search-result-salary"[^>]*>.*?<strong[^>]*>(.*?)</strong>', block, re.S)
        posted = re.search(r'data-test="search-result-publicationDate"[^>]*>.*?<strong[^>]*>(.*?)</strong>', block, re.S)
        yield dict(source="nhs", source_id=link.group(2), title=plain(link.group(3)),
                   url="https://www.jobs.nhs.uk" + html.unescape(link.group(1)).split("?")[0],
                   location=plain(place.group(1)) if place else "", description="",
                   posted_at=plain(posted.group(1)) if posted else "",
                   company_name=plain(employer.group(1)) if employer else "NHS employer",
                   salary=plain(salary.group(1)) if salary else "")


def jobspy(sites, query, location):
    """LinkedIn, Indeed and Glassdoor through the open-source python-jobspy package."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        raise ValueError("Install python-jobspy to search LinkedIn, Indeed and Glassdoor")
    place = location if location and location.lower() not in ("uk", "united kingdom") else "United Kingdom"
    frame = scrape_jobs(site_name=list(sites), search_term=query, location=place,
                        results_wanted=settings.number("JOBSPY_RESULTS", 30),
                        hours_old=settings.number("DISCOVERY_HOURS_OLD", 72), country_indeed="UK",
                        linkedin_fetch_description=True, description_format="markdown", verbose=0)
    for record in frame.to_dict("records"):
        clean = {k: ("" if v is None or (isinstance(v, float) and v != v) else v) for k, v in record.items()}
        emails = clean.get("emails") or ""
        if isinstance(emails, (list, tuple)):
            emails = ",".join(emails)
        direct = str(clean.get("job_url_direct") or "")
        salary = ""
        if clean.get("min_amount"):
            salary = f"{clean.get('min_amount')}-{clean.get('max_amount')} {clean.get('currency', '')} {clean.get('interval', '')}".strip()
        yield dict(source=str(clean.get("site") or "jobspy"), source_id=str(clean.get("id") or clean.get("job_url")),
                   title=str(clean.get("title") or ""), url=str(clean.get("job_url") or ""),
                   location=str(clean.get("location") or ""), description=str(clean.get("description") or "")[:20000],
                   posted_at=str(clean.get("date_posted") or ""), company_name=str(clean.get("company") or "Unknown employer"),
                   apply_url=direct, easy_apply=not direct, emails=str(emails),
                   company_url=str(clean.get("company_url_direct") or ""), salary=salary)


SEARCHERS = {"reed": reed, "adzuna": adzuna, "jooble": jooble, "remotive": remotive, "arbeitnow": arbeitnow,
             "themuse": themuse, "himalayas": himalayas, "jobicy": jobicy, "remoteok": remoteok, "nhs": nhs}
JOBSPY_SITES = ("linkedin", "indeed", "glassdoor")
# Bulk feeds return everything; filter by title before saving.
FILTERED = {"arbeitnow", "themuse", "remoteok", "remotive", "himalayas", "jobicy"}


def save_job(job):
    job = dict(job)
    company_name = (job.pop("company_name", "") or "Unknown employer").strip()[:200]
    domain = channels.host_of(job.get("company_url", "")) if job.get("company_url") else ""
    if domain and channels.board_for("https://" + domain):
        domain = ""
    company_id = db.upsert_company(company_name, domain=domain.removeprefix("www."), ats="manual")
    return db.upsert_job(company_id, **job)


def search(queries, location="United Kingdom", boards=None):
    """Run keyword search across boards. Returns a summary with per-board counts and errors."""
    wanted = set(boards or []) - {"all", ""}
    use = lambda name: not wanted or name in wanted
    summary = {"jobs_seen": 0, "by_board": {}, "errors": []}
    for query in list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))[:6]:
        sites = [s for s in JOBSPY_SITES if use(s)]
        batches = [("jobspy:" + ",".join(sites), lambda q=query, s=sites: jobspy(s, q, location))] if sites else []
        batches += [(name, lambda q=query, fn=fn: fn(q, location)) for name, fn in SEARCHERS.items() if use(name)]
        for name, producer in batches:
            try:
                for job in producer():
                    if not job.get("url") or not job.get("title") or not job.get("source_id"):
                        continue
                    if job["source"] in FILTERED and not relevant(job["title"], query):
                        continue
                    save_job(job)
                    summary["jobs_seen"] += 1
                    summary["by_board"][job["source"]] = summary["by_board"].get(job["source"], 0) + 1
            except Exception as exc:  # one failing board must never stop the others
                summary["errors"].append({"source": name, "query": query, "error": str(exc)[:300]})
            time.sleep(0.5)
    db.log_event("search", summary)
    return summary


def search_for_loops(loop_rows):
    """One search per distinct (titles, location, boards) across due loops."""
    plans = {}
    for loop in loop_rows:
        titles = json.loads(loop["titles"])[:3]
        boards = json.loads(loop["boards"] or "[]")
        key = (loop["location"] or "United Kingdom", json.dumps(sorted(boards)))
        plans.setdefault(key, set()).update(titles)
    results = []
    for (location, boards), titles in plans.items():
        results.append(search(sorted(titles), location, json.loads(boards)))
    return results


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def search_tasks():
    """Search pages for the Chrome extension to open (boards that block servers)."""
    tasks, seen = [], set()
    for loop in db.rows("SELECT * FROM loops WHERE active=1"):
        boards = set(json.loads(loop["boards"] or "[]")) - {"all", ""}
        location = loop["location"] or "United Kingdom"
        uk_wide = location.strip().lower() in ("uk", "united kingdom", "any", "remote", "")
        for title in json.loads(loop["titles"])[:3]:
            for board, template in BROWSER_SEARCH.items():
                if boards and board not in boards:
                    continue
                if uk_wide:
                    in_loc = ""
                elif board in ("cv-library", "reed"):
                    in_loc = "-in-" + _slug(location)
                else:
                    in_loc = "/in-" + _slug(location)
                url = template.format(q=urllib.parse.quote(title), loc=urllib.parse.quote("" if uk_wide else location),
                                      slug=_slug(title), in_loc=in_loc)
                if url not in seen:
                    seen.add(url)
                    tasks.append({"board": board, "url": url, "loop_id": loop["id"], "query": title})
    return tasks


def harvest(data):
    """Jobs collected by the Chrome extension from a page in the user's browser."""
    items = data.get("jobs") or []
    if not isinstance(items, list):
        raise ValueError("jobs must be a list")
    saved = 0
    for item in items[:300]:
        url = str(item.get("url") or "").strip()
        title = " ".join(str(item.get("title") or "").split())[:250]
        if not url.startswith("https://") or not title:
            continue
        board = str(item.get("board") or channels.board_for(url) or "web")[:30]
        save_job({"source": board, "source_id": str(item.get("id") or url)[:400], "title": title, "url": url,
                  "location": str(item.get("location") or "")[:200], "description": str(item.get("description") or "")[:20000],
                  "company_name": str(item.get("company") or "Unknown employer")[:200],
                  "easy_apply": bool(item.get("easy_apply")), "poster_name": str(item.get("poster_name") or "")[:120],
                  "apply_url": str(item.get("apply_url") or "")[:1000], "salary": str(item.get("salary") or "")[:120]})
        saved += 1
    db.log_event("harvest", {"saved": saved, "board": data.get("board")})
    return {"saved": saved}


def sync_companies():
    summary = {"companies": 0, "jobs_seen": 0, "errors": []}
    for company in db.rows("SELECT * FROM companies WHERE ats IN (%s)" % ",".join("?" * len(ADAPTERS)), tuple(ADAPTERS)):
        try:
            entries = list(ADAPTERS[company["ats"]](company["ats_slug"]))
            for job in entries:
                if job.get("title") and job.get("url"):
                    db.upsert_job(company["id"], **{k: v for k, v in job.items() if k != "company_name"})
                    summary["jobs_seen"] += 1
            db.run("UPDATE companies SET last_synced_at=? WHERE id=?", (db.utc_now(), company["id"]))
            summary["companies"] += 1
        except (urllib.error.URLError, KeyError, ValueError, TypeError, ElementTree.ParseError) as exc:
            summary["errors"].append({"company": company["name"], "error": str(exc)[:300]})
    db.log_event("sync_companies", summary)
    return summary


def sync_aggregators(query="data analyst"):
    summary = {"jobs_seen": 0, "errors": []}
    for name, generator in (("reed", reed), ("adzuna", adzuna)):
        try:
            for job in generator(query=query) or []:
                if not job.get("url") or not job.get("title"):
                    continue
                save_job(job)
                summary["jobs_seen"] += 1
        except (urllib.error.URLError, KeyError, ValueError, TypeError) as exc:
            summary["errors"].append({"source": name, "error": str(exc)[:300]})
    db.log_event("sync_aggregators", summary)
    return summary


def import_jobs_csv(content):
    """Ingest user-provided job exports (including JobSpy CSV column conventions)."""
    reader=csv.DictReader(io.StringIO(content))
    count,skipped=0,0
    if not reader.fieldnames:
        raise ValueError("CSV requires a header row")
    for index,record in enumerate(reader):
        if index>=5000:
            raise ValueError("Import limit is 5,000 job records per file")
        job_title=(record.get("title") or record.get("job_title") or "").strip()
        url=(record.get("job_url") or record.get("url") or "").strip()
        company_name=(record.get("company") or record.get("company_name") or "").strip()
        if not job_title or not url.startswith("https://") or not company_name:
            skipped+=1
            continue
        company_id=db.upsert_company(company_name,ats="manual")
        origin=normalize_source(record.get("site") or record.get("source") or "csv")
        db.upsert_job(company_id,"csv-"+origin,record.get("id") or record.get("job_id") or url,
                      job_title,url,record.get("description") or "",record.get("location") or "",
                      record.get("date_posted") or "",apply_url=record.get("job_url_direct") or "",
                      emails=record.get("emails") or "")
        count+=1
    result={"jobs_imported":count,"skipped":skipped}
    db.log_event("import_jobs_csv",result)
    return result


def normalize_source(value):
    return re.sub(r"[^a-z0-9-]", "", str(value).lower())[:35] or "csv"
