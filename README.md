# Brit Institute — Placement OS

Self-hosted, LoopCV-style job automation for UK learner cohorts. Each learner gets **search loops**. Every 2 hours a loop:

1. **Searches 25+ job sources:** LinkedIn, Indeed, Glassdoor, NHS Jobs, Reed, Adzuna, Jooble, Totaljobs, CWJobs, Jobsite, CV-Library, Guardian Jobs, remote boards, and company ATS feeds.
2. **Matches** jobs to the learner by job title, location, seniority, skills and right-to-work / sponsorship rules.
3. **Tailors the CV** to each job with Groq, using only the learner's real facts.
4. **Auto-applies:**
   * Easy Apply on LinkedIn / Indeed / Glassdoor and UK boards, through the Chrome extension in the learner's own browser.
   * Company career sites (Greenhouse, Lever, Ashby, Workable, SmartRecruiters…), through the server's headless browser.
   * Groq answers screening questions.
5. **Emails the recruiter:**
   * Finds their address: the advert, the company website, or name and email patterns checked against the company's mail server.
   * Sends a personalised email with the tailored CV attached.

The backend is Python (standard library + SQLite) with a local dashboard on `http://127.0.0.1:8770`.

## Quick start (Windows)

1. Double-click **`Start Placement.cmd`** (or run `start.ps1`). Open http://127.0.0.1:8770 and paste the admin key from `data\admin-token.txt`.
2. **Settings:**
   * Paste your **Groq API key** and press *Test Groq*.
   * Add SMTP details for recruiter emails. Gmail: `smtp.gmail.com`, port `465`, with a Google *app password*.
   * Press *Send SMTP test*.
   * Optional: Reed / Adzuna / Jooble API keys.
3. **CV builder / learners → Add learner.** Fill the whole profile: phone, location, postcode, LinkedIn URL, skills, work history, education, salary expectation, notice period and right to work. Tick consent. The more facts here, the more questions are answered correctly. Optionally upload their own CV in **CV checker**; tailoring uses it as source material.
4. **My loops → Create new loop:**
   * Choose the learner, job titles, location and job boards.
   * Mode **Auto-apply**, plus *Tailor CV* and *Email recruiters*.
   * Saving searches the boards immediately.
5. **Install the Chrome extension for each learner** (below). Applications on LinkedIn, Indeed, Glassdoor and UK boards are made from it.

First-time install on a new PC:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-optional.txt
$env:PLAYWRIGHT_BROWSERS_PATH="data\playwright"; .venv\Scripts\python -m playwright install chromium
```

## Chrome extension (LinkedIn, Indeed, Glassdoor, UK boards)

Job boards only accept applications from a logged-in candidate, so the extension runs inside **that learner's Chrome profile**.

1. Create a Chrome profile per learner and sign in to LinkedIn, Indeed, Glassdoor, Reed, Totaljobs and CV-Library as that learner.
2. Open `chrome://extensions` → Developer mode → **Load unpacked** → select the `extension` folder.
3. In the popup:
   * Enter the backend URL (`http://127.0.0.1:8770`) and the admin key, then press *Connect*.
   * Choose the learner.
   * Switch on **Auto-apply to queued jobs** and **Search job boards every 2 hours**.
4. Keep Chrome open. Every minute the runner:
   * claims the next queued job for that learner;
   * opens it in a background tab and clicks Easy Apply / Apply now;
   * fills each step (profile → saved answers → Groq), uploads the tailored CV and presses Next / Review / Submit;
   * confirms the receipt ("Your application was sent…") and reports back.

   It pauses 45–120 s between applications and stops at the daily limit (Settings).
5. On any job page you can also press **Auto-apply to this job** or **Fill only (I submit)**. *Save jobs listed here* imports every job on a search-results page.

Some dropdowns (React-based, e.g. Greenhouse) ignore scripted clicks. For those the extension sends real keyboard and mouse input through Chrome's debugger API, and Chrome shows a "started debugging this browser" bar for a moment.

## Where each application goes

| Job found on | Applied by | How |
|---|---|---|
| LinkedIn Easy Apply | Extension | Multi-step Easy Apply modal |
| LinkedIn / Indeed / Glassdoor with "Apply on company site" | Server browser (or extension) | Follows the link to the employer's ATS |
| Indeed Apply, Glassdoor Easy Apply | Extension | Indeed SmartApply flow |
| Reed, Totaljobs, CWJobs, Jobsite, CV-Library, Guardian, NHS Jobs, Civil Service, Workday | Extension | Board's own apply flow (needs the learner's account) |
| Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, company sites, Adzuna/Jooble/remote-board redirects | Server browser | Headless Chromium |

If the server browser meets a CAPTCHA or a login page, it hands the job to the extension to retry in the learner's real browser. Anything that still can't be finished goes to **Need attention**, with the reason, e.g. a question the AI couldn't answer, a login, or a CAPTCHA. Fix the cause and press *Retry*.

## Job sources

| Source | Needs | Notes |
|---|---|---|
| LinkedIn, Indeed UK, Glassdoor UK | nothing (python-jobspy) | Includes the employer's direct apply link when there is one |
| NHS Jobs, Remotive, Arbeitnow, The Muse, Himalayas, Jobicy, RemoteOK | nothing | Remote boards filtered to UK-eligible roles |
| Reed, Adzuna, Jooble | API key in Settings | |
| Totaljobs, CWJobs, Jobsite, CV-Library, Reed site, Guardian Jobs, logged-in LinkedIn/Indeed | Chrome extension | These sites block servers; the extension opens the loop's searches in background tabs |
| Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Workday | company slug (Target companies) | Workday slug: `tenant|wd3|site` |
| Anything else | Jobs → Import CSV, or "Save jobs listed here" in the extension | |

## Recruiter emails

For each application in an auto loop with *Email recruiters* on:

1. Emails printed in the advert (or returned by the board).
2. Role inboxes published on the company website.
3. The hiring contact's name (from the advert / LinkedIn poster) plus email patterns. Verified by SMTP when outbound port 25 is open; most home broadband blocks it, and then the most common pattern is used.
4. `careers@` / `recruitment@`… on the company's mail domain (MX checked).

Groq writes a 90–150 word email in the loop's template style. It goes from your SMTP account with the learner's name as sender and Reply-To set to the learner. The tailored CV is attached and an opt-out line is included. Replies are tracked with *Check replies* (IMAP), and "opt out" replies are suppressed automatically. Daily cap per learner is in Settings.

## Screening questions and CV tailoring

* **Questions:** saved answers are used first. Everything else goes to Groq in one request per form step, with the learner's profile and the job description. Option fields must be answered with one of the given options. Right-to-work and sponsorship come strictly from the profile. Equal-opportunity questions get "Prefer not to say". AI answers are saved in **Questions**, where you can edit or un-approve them. Motivation and cover-letter answers are written fresh for each job.
* **Tailored CV:** Groq rewrites the headline, summary, skill order and bullets around the job's keywords. Any employer, school or skill that is not in the learner's own data is removed before the PDF is generated, so tailoring cannot invent experience. Every tailored CV is kept in **CV checker**.

## Running in the background

`start.ps1` runs the server hidden. Its scheduler (every 2 minutes):

* runs due loops (search + match + queue);
* applies to queued company-site jobs in the server browser;
* sends recruiter emails.

The PC and Chrome must stay on. For a VPS, use `docker compose` with `PLACEMENT_DOCKERFILE=Dockerfile.auto`. The extension still runs on a machine with each learner's Chrome profile.

CLI: `python app.py run-loops | autoapply-queue | outreach | poll-replies | sync | sync-sponsors`

## Risks and limits

* **Account restrictions:** LinkedIn's and Indeed's user agreements prohibit automated applying. Accounts that apply too fast can be restricted. Keep the daily limit moderate (default 40) and the random pause on.
* **What stops an application:** CAPTCHAs, required logins, account creation (Workday), and questions the profile can't answer. These go to *Need attention* rather than being guessed.
* **Site changes:** job boards change their pages. The engine finds fields by labels and roles rather than fixed selectors, but a redesign can still break a step.
* **Open tracking:** email open tracking needs a publicly reachable server, so it isn't included.
* **Consent:** only apply and email with each learner's explicit consent, and honour opt-outs (UK GDPR / PECR).

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
node tests/test_extension.cjs
node tests/test_dashboard.cjs
node tests/test_dashboard_runtime.cjs
```

`test_engine_browser.py` drives the real apply engine in Chromium against local copies of a multi-step ATS form, an Easy Apply modal and a React dropdown that ignores scripted input. `test_extension_browser.py` loads the actual extension and lets its runner apply. It is skipped where Chromium can't load extensions. Groq and SMTP are stubbed in tests.

## Code map

`app.py` API/CLI/scheduler · `placement/sources.py` job search · `channels.py` routing · `loops.py` loops · `matching.py` scoring · `applyqueue.py` queue/claims/results · `autoapply.py` server browser · `questions.py` AI answers · `tailor.py` tailored CVs · `recruiters.py` recruiter emails · `outreach.py` SMTP/IMAP · `ai.py` Groq · `settings.py` Settings · `extension/engine.js` apply engine (shared) · `extension/background.js` runner
