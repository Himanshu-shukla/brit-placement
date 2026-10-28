"""Decide where an application is submitted.

* "extension": job boards that need the candidate's own logged-in session
  (LinkedIn, Indeed, Glassdoor, Reed, Totaljobs...). Applied by the Chrome extension
  running in that candidate's Chrome profile.
* "server": employer/ATS application pages that need no account. Applied by the
  server's Playwright browser (or the extension, whichever claims it first).
"""

from urllib.parse import urlsplit

LOGIN_BOARDS = {
    "linkedin.com": "linkedin", "indeed.com": "indeed", "glassdoor.com": "glassdoor", "glassdoor.co.uk": "glassdoor",
    "reed.co.uk": "reed", "totaljobs.com": "totaljobs", "cwjobs.co.uk": "cwjobs", "jobsite.co.uk": "jobsite",
    "cv-library.co.uk": "cv-library", "jobs.theguardian.com": "guardian", "jobs.nhs.uk": "nhs",
    "civilservicejobs.service.gov.uk": "civilservice", "myworkdayjobs.com": "workday",
    "welcometothejungle.com": "otta", "escapethecity.org": "escapethecity", "hays.co.uk": "hays",
}
# Listing sites whose job URL is a redirect to the employer; the browser follows them.
REDIRECT_BOARDS = {"adzuna.co.uk": "adzuna", "adzuna.com": "adzuna", "jooble.org": "jooble",
                   "remotive.com": "remotive", "remoteok.com": "remoteok", "himalayas.app": "himalayas",
                   "jobicy.com": "jobicy", "themuse.com": "themuse", "arbeitnow.com": "arbeitnow"}
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com", "recruitee.com",
             "personio.de", "personio.com", "teamtailor.com", "bamboohr.com", "jobvite.com", "icims.com",
             "breezy.hr", "applytojob.com", "pinpointhq.com", "jazzhr.com", "rippling.com", "workday.com")


def host_of(url):
    return (urlsplit(str(url or "")).hostname or "").lower()


def _lookup(host, table):
    return next((name for domain, name in table.items() if host == domain or host.endswith("." + domain)), None)


def board_for(url):
    host = host_of(url)
    return _lookup(host, LOGIN_BOARDS) or _lookup(host, REDIRECT_BOARDS)


def needs_login(url):
    return bool(_lookup(host_of(url), LOGIN_BOARDS))


def is_ats(url):
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in ATS_HOSTS)


def target(job):
    """The URL an application should start from: a direct employer link beats the board listing,
    except where the board has its own one-click apply (Easy Apply / Indeed Apply)."""
    direct = str(job.get("apply_url") or "")
    if direct.startswith("https://") and not needs_login(direct) and not job.get("easy_apply"):
        return direct
    return str(job.get("url") or direct)


def channel_for(url):
    return "extension" if needs_login(url) else "server"
