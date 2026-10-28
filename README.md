# Brit Institute — Placement OS

A self-hosted, zero-subscription, LoopCV-inspired placement-automation system for UK data-analytics learner cohorts. Built using **Python's standard library** (no core backend install required), **SQLite**, a dashboard, a two-hour search-loop scheduler, and a **Chrome Manifest V3 extension**.

> **Honest scope:** This is an independent implementation of common job-search automation workflows, **not** a reproduction of LoopCV's proprietary backend or paid integrations. Tests cover local API and mock ATS/browser fixtures. It cannot promise job offers or 30+ job-board access without permitted data sources. LinkedIn/Indeed have not been verified in real authenticated sessions and the extension **does not automatically submit on those platforms**. Automatic submission is opt-in for exactly allowlisted employer domains only and stops when information or evidence is missing. External site changes can break adapters.

## Fast start (Windows)

1. Install [Python 3.11+](https://www.python.org/downloads/) and ensure `python` is in PATH.
2. Unzip this folder, then in PowerShell:

   ```powershell
   cd brit-placement
   python app.py serve
   ```

3. Open **http://127.0.0.1:8765** in Chrome.
4. Copy the **admin key** printed in the terminal into the dashboard's prompt. Save the key privately; it also lives in `data/admin-token.txt`.
5. Go to **CV builder / learners → Add learner**; add verified skills, consent, work authorisation and CV content.
6. Go to **Companies → Add company**. For public feeds use `greenhouse`, `lever` or `ashby` and the employer's board slug. Use `manual` for employers with other sites. Click **Sync feeds**.
7. Open **My loops → Create new loop**. Choose learner, title, location, filters, CV, review/auto mode and two-hour frequency. Click **Save and run**.
8. Open **All matches**, **Application board**, **Questions**, and **CV checker** to manage the pipeline.

The `serve` command starts a background scheduler that checks due loops every two minutes. Each loop is due every 120 minutes by default. It de-duplicates matches and applications across cycles. Stop the Python process to stop the scheduler; a PC left switched off cannot run background tasks.

For a dry run with fake data only:

```powershell
python app.py seed-demo
python app.py match
python app.py serve
```

For the actual cohort, omit `seed-demo`. It creates an explicitly fictional learner and vacancy.

## Install the Chrome extension

1. In Chrome, open **chrome://extensions**.
2. Enable **Developer mode** (upper right), then **Load unpacked**.
3. Select the **`extension`** directory inside this project.
4. Keep `python app.py serve` running; open the extension popup and enter your private backend key.
5. Open a real LinkedIn or Indeed job posting in Chrome. Open its Easy Apply dialog and select a consented learner.
6. Upload a CV to **CV checker**, select it in the extension, and save verified screening answers in **Questions**.
7. Click **Fill visible fields**. It can fill common profile fields, attach a selected CV and reuse approved answers where compatible. Repeat after each Easy Apply step; manually review and submit on LinkedIn/Indeed.
8. For a **permitted employer-owned application site**, enter its exact hostname in **Allowed employer hostnames**, then use **Apply on approved employer page**. The extension requests confirmation before attempting the site's submit button.
9. Use **Save job & queue for candidate** to track the posting. Only verified employer receipts advance the application to submitted.

The extension uses only an **explicit user click**, `activeTab` permission, and the browser's current page. It does not read passwords or cookies and cannot bypass CAPTCHA/login walls. File upload and custom multi-step forms are best-effort. It pauses for unanswered mandatory/sensitive questions. On LinkedIn/Indeed/Glassdoor it never clicks Submit; their platform rules restrict third-party automation. A browser session and page interaction are necessary for extension features.

## Four-step loops and application modes

1. **Search info:** learner, role titles, preferred location, remote/hybrid, experience, job type, salary, preferred boards, CV variant.
2. **Email template:** reviewable recruiting enquiry text, stored per loop.
3. **Settings:** minimum match score, 2-hour or slower cycles, employer exclusions, review or auto mode.
4. **Review:** save the loop and immediately score the discovered vacancies.

**Review:** matches stay in All Matches for manual selection. **Auto:** eligible matches can be queued without repetitive staff work, subject to consent and strict sponsorship filters. Server-side submission additionally requires Playwright, a valid saved CV, `AUTO_SUBMIT_ENABLED=1`, and an exact `AUTO_APPLY_ALLOWED_HOSTS` entry for the specific employer page. The server will not automatically submit to job boards that forbid third-party automation.

Loop modes do not manufacture answers or claim that a queued application was sent. To view outcomes, use **Application board**, **My applications**, and **Analytics**.

## Supported discovery sources

| Source | Configuration | Important limitation |
|---|---|---|
| Greenhouse | Employer board slug | Public GET job feed only; submission APIs need employer credentials |
| Lever | Employer posting slug | Public GET feed only; candidate POST needs employer permissions |
| Ashby | Employer job board slug | Public posting feed; not all employers expose one |
| Workable | Optional slug | Public endpoint availability varies; may fail for some tenants |
| Reed | `REED_API_KEY` environment variable | Subject to Reed developer/API approval and terms |
| Adzuna | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Evaluation quotas/licensing apply to commercial use |
| LinkedIn / Indeed | Chrome extension on a visited posting | Manual discovery and reviewed form assistance; not unattended scraping |

**JobSpy interoperability:** If you already have a CSV export from a tool such as `python-jobspy` that you are permitted to use, open **Jobs → Import jobs CSV**. It understands `title`, `company`, `location`, `job_url`, `description`, `site` and `date_posted` columns. This does not run a scraper for you or bypass a platform's rules.

Default employer feed syncing works without paid APIs. A broad market-wide feed is **not** guaranteed with zero credentials.

## UK sponsorship filter

- Download the official **[Home Office register of licensed worker sponsors](https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers)** as CSV and upload it via **Overview → Import sponsor list**.
- Or run `python app.py sync-sponsors` to automatically find the currently linked CSV on the official page. UK government document layout can change; manual import remains the fallback.
- The matcher only counts a company as a verified sponsor on an **exact normalised name match**. Corporate-group names can be different. A licence does **not** establish that the specific role is sponsor-eligible.
- If an applicant needs sponsorship and company matching is not verified, the application is flagged **review**, not automatically considered eligible.

## Published recruitment contact discovery

In **Companies → Scan public contact page**, the app checks `robots.txt`, stays on the same company-owned host, reads a maximum of four HTML pages, and finds **explicitly published role-based** addresses such as `careers@`, `jobs@`, or `recruitment@`. It records career/contact links as alternate outreach routes.

It does not scrape personal LinkedIn profiles, guess private email patterns, bypass access controls, or claim that every company publishes an HR mailbox. Where recruitment contacts are not public, apply through the official vacancy.

## Reviewed outreach and email replies

The **Outreach** tab can draft candidate-authorised, one-to-one correspondence for the specific company/vacancy. It prevents duplicate drafts, enforces candidate consent, and supports an opt-out suppression table. Emails **never send** until a person reviews and explicitly approves the draft.

Provide authorised SMTP environment variables (see `.env.example`) in PowerShell, e.g.:

```powershell
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "465"
$env:SMTP_USERNAME = "placements@your-domain.example"
$env:SMTP_PASSWORD = "YOUR_SMTP_APP_PASSWORD"
$env:SMTP_FROM = "placements@your-domain.example"
python app.py serve
```

These are placeholders, not live credentials. A Gmail/Google Workspace account may require specific authentication settings; do not store credentials in the project source or commit them.

For optional reply tracking set `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`, then run `python app.py poll-replies`. Replies are matched by the `[BRIT-ID]` tag added to each sent email subject. Explicit opt-out replies are automatically suppressed. The reader checks the latest 200 messages for the last 30 days and stores a limited reply snippet.

Follow applicable UK PECR/UK GDPR requirements for outbound communications, keep data minimised, and honour opt-outs. Sending mail does not imply an application has been made.

## CV generation and ATS browser assistance

**CV builder / learners → Print view** generates clean HTML from verified learner fields, while **Download PDF** generates A4 directly when free optional `reportlab` is installed (`pip install reportlab`). The **CV checker** can store PDF or DOCX versions, check readable sections and compare verified skills against a selected job. PDF checking requires `pypdf`; a missing dependency results in an actionable error. The browser's **Save as PDF** print dialog is an alternative without dependencies. No AI invents experience, employment or qualifications.

For optional browser assistance on employer-hosted application pages (not LinkedIn/Indeed):

```powershell
python -m pip install playwright
python -m playwright install chromium
python -m placement.browser_worker 1
```

Replace `1` with an application ID. This opens a visible browser, fills common fields and pauses for manual checks and submission; it never marks the job submitted without a user-entered confirmation.

### Explicit employer-site auto submission

For unattended applications on an employer domain that permits your automation, install Playwright and Chromium on the machine running the backend, then configure:

```powershell
$env:AUTO_SUBMIT_ENABLED = "1"
$env:AUTO_APPLY_ALLOWED_HOSTS = "jobs.example.org,careers.example.org"
python app.py serve
```

The domains above are placeholders. Add only real employer application hostnames for which automation is permitted. Auto mode creates new queued applications; the scheduler processes a bounded number on allowlisted hostnames using headless Chromium. It rechecks the hostname after redirects, uses the CV variant associated with the loop, and stops on CAPTCHA, missing required fields, unavailable CVs, unclear submit buttons, and unverified submission outcomes. Each automatic attempt is logged and **never retried without explicit review**. Check **Analytics → Application attempts** for results. It does not promise every employer site will work.

### Optional local AI, no per-application API fees

Install [Ollama](https://ollama.com/), download a small instruction model appropriate for your RAM/VRAM, and set `OLLAMA_MODEL` to the local model's name before starting the server. The **Matches → Local AI refine top 10** button will refine already-eligible candidates' rankings, bounded to ±15 points; it will not override sponsorship/consent exclusions. The **Outreach → AI tailor** button drafts a personalised email. All model content must be checked against verified candidate evidence before use. Offline deterministic matching and drafted emails work without Ollama.

## VPS / Docker deployment — computer can be off

If you already have a VPS, Docker Compose can keep discovery and scheduling running without your Windows PC. The default build runs the dashboard and matching, and the optional `Dockerfile.auto` includes Chromium.

```bash
docker compose up -d --build
docker compose logs -f placement
```

For the larger browser-automation image, put `PLACEMENT_DOCKERFILE=Dockerfile.auto` in a local `.env` (copy `.env.example` and replace placeholders), along with the explicit employer allowlist and submission flag, then rebuild. Playwright's official Docker image guidance recommends running Chromium as a non-root user and using appropriate container sandboxing when visiting untrusted sites. Review your host's sandbox policy before enabling it.

Compose publishes only to the VPS's loopback interface: `127.0.0.1:8765`. Access it from Windows over an SSH tunnel:

```powershell
ssh -L 8765:127.0.0.1:8765 youruser@your-vps-host
```

Then open `http://127.0.0.1:8765` locally. Do **not** expose the application directly to the Internet without proper HTTPS, dedicated user authentication, audit logs and data-protection controls. The current admin key protects the small internal installation; it is not a multi-tenant security system.

## Daily job search and scheduling

Manual commands:

```powershell
python app.py sync
python app.py match
python app.py run-loops
python app.py autoapply-queue
python app.py sync-sponsors
python app.py poll-replies
```

The `serve` process runs its own two-hour loop scheduler. **Do not also schedule `run-loops` in Windows Task Scheduler or cron** unless you have disabled the embedded scheduler (`PLACEMENT_SCHEDULER=0`)—otherwise two processes could compete for the same database. For manually supervised deployments, you can schedule `run-loops`, `sync` and `match` yourself with one worker, a persistent disk and a predictable working directory. Schedule sponsor-list refresh weekly.

For Linux cron:

```cron
0 */2 * * * cd /path/to/brit-placement && /usr/bin/python3 app.py run-loops
0 7 * * 1 cd /path/to/brit-placement && /usr/bin/python3 app.py sync-sponsors
```

Do not schedule on ephemeral GitHub Actions runners unless you also supply persistent, private storage; otherwise each run may lose the SQLite database.

## Running tests

From the project directory:

```powershell
python -m unittest discover -s tests -v
node tests/test_extension.cjs
node tests/test_apply_engine.cjs
node tests/test_dashboard.cjs
node --check static/app.js
node --check static/advanced.js
node --check extension/popup.js
```

Tests exercise local HTTP workflows, repeated loops, mock public ATS feeds, CV variants, PDF inspection, question approval, email-sending approval, consent checks, sponsorship screening, duplicate job/application prevention, and simulated employer-form submission. They do **not** validate Gmail credentials, production employer responses, a real signed-in LinkedIn/Indeed session, live Playwright browser execution, email-open tracking, or all custom ATS portals. Run a small pilot on permitted employer forms before enabling unattended submissions.

## Data and security

- Private SQLite records and API key are in `data/` and excluded by `.gitignore`.
- Backend listens on **127.0.0.1** by default; do not expose the port directly to the Internet.
- Treat admin key as a password; the extension stores it in `chrome.storage.local` on your Chrome profile.
- Candidate consent must be obtained **before** representing or applying on their behalf.
- Store only real evidence of submissions and hiring; the system cannot ethically or technically guarantee 5 placements in the first month.
- Back up the `data/` folder securely; restrict access to candidate personal details and remove outdated records under your retention policy.

## Troubleshooting

- **401 in dashboard/extension:** restart server, retrieve admin key from terminal, reenter it.
- **Extension connection error:** ensure `python app.py serve` is still running on port 8765, local firewall isn't blocking it, and you loaded the `extension` folder rather than the repository root.
- **A feed returns 404 or empty:** verify exact employer board slug; some companies do not use these public services.
- **Reed/Adzuna empty:** no licensed API credentials supplied, or the API returned a rate-limit/licensing error.
- **No emails discovered:** the employer might have no published role mailbox, or disallows crawling through `robots.txt`.
- **No matches:** check role title, permissions, seniority and sponsorship needs; `review` jobs require manual screening.
- **Unfilled Easy Apply question:** the field is custom/hidden or outside the accessible DOM. Continue manually. Do not bypass security checks.

## Implementation map

`app.py` HTTP API and CLI · `placement/db.py` SQLite · `placement/loops.py` 2-hour cycles · `placement/sources.py` sources · `placement/matching.py` screening · `placement/contacts.py` published contact collector · `placement/outreach.py` email/IMAP · `placement/questions.py` screening approvals · `placement/cv_files.py` CV variants · `placement/autoapply.py` opt-in employer ATS submission · `static/` dashboard · `extension/` Chrome popup and form engine · `compose.yaml` persistent VPS deployment · `tests/` local tests.
