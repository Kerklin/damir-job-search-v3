damir-job-search

A job-search system that runs entirely on **GitHub**: no AI, no LLM, no paid services and no installation on your computer.
Everything is rule-based and traceable to one verified profile (`profile.json`).

Owner: Damir Hadžić, PhD, PMP — Construction Engineer / Project Manager, Sarajevo, BiH.

## What it does

| Function | How it works on GitHub |
|---|---|
| Find jobs | Daily scheduled run fetches ReliefWeb jobs (+ any RSS feeds in `config.json`) |
| Rank jobs | Fit score 1–100: requirements 50 · experience 20 · skill keywords 15 · location 15; hard filters |
| Dashboard | Published to GitHub Pages by the workflow: ranked jobs, applications funnel, skill gaps, one-click **Apply** |
| Apply | Open an **Apply for a job** issue and paste the posting. Within ~1 minute you get: HR screening table, tailored CV (Word + text), cover letter (Word + text), ATS keyword report, copy-paste form answers, salary check |
| Track | Issue labels `status:submitted`, `status:screening`, `status:interview`, `status:offer`, `status:rejected`, `status:withdrawn` |
| Interview prep | Add label `status:interview` → competency questions with STAR answers, technical questions, gap bridge answers, panel questions |
| Upskill | Gap heatmap + learning plan from all ranked jobs and applications (dashboard + `data/upskill.md`) |
| Privacy guard | Every run fails if a phone number or personal e-mail appears in the repository |

Rule-based honesty: the engine only reuses sentences from `profile.json`. It never invents results; skills known only from training are
marked **Partly (training only)**; missing skills are listed as gaps. Contact details appear as `[EMAIL]` / `[PHONE]` — fill them in Word.

## Files

| File | Purpose |
|---|---|
| `profile.json` | Your verified CV facts, headlines, competencies, STAR examples |
| `config.json` | Job sources, scoring weights, filters, skill dictionary, learning resources, salary links |
| `interview_bank.json` | Competency questions and triggers |
| `templates.md` | Cover letter templates (English, Bosnian) |
| `jobsearch.py` | The engine (Python standard library only) |
| `.github/workflows/job-search.yml` | The automation and the Pages deployment |
| `WORKFLOW-job-search.yml` | Identical backup copy of the workflow (the self-test checks they match) |
| `.github/ISSUE_TEMPLATE/apply.yml` | Apply form (re-created by the setup task if missing) |
| `docs/`, `data/`, `applications/` | Generated output |

## Setup (15 minutes, browser only)

### 1. Create and upload
1. github.com → **+** → **New repository** → `damir-job-search` → **Public** → no README → **Create repository**.
2. Click **uploading an existing file**, drag the *contents* of the unzipped folder (including `.github`) → **Commit changes**.
3. If `.github` did not upload: **Add file → Create new file**, type `.github/workflows/job-search.yml`, paste the content of
   `WORKFLOW-job-search.yml`, **Commit**. (The issue form is created automatically in step 3.)

### 2. Allow the workflow to write
**Settings → Actions → General → Workflow permissions** → **Read and write permissions** → **Save**.
The workflow itself requests only what each job needs (`contents` + `issues` for the run job; `pages` + `id-token` for the deploy job).

### 3. Run setup
**Actions → job-search → Run workflow → task: setup → Run workflow**. Creates the Apply form, labels and the first dashboard.
(The deploy job may fail on this first run — that is expected until step 4 is done.)

### 4. Publish the dashboard with GitHub Actions Pages
1. **Settings → Pages → Build and deployment → Source** → select **GitHub Actions**.
   Do **not** select *Deploy from a branch*: that starts GitHub's built-in *pages build and deployment* workflow,
   which still uses Node.js 20 actions and `ubuntu-latest` and produces the deprecation warnings.
2. **Settings → Environments → github-pages → Deployment branches and tags** → make sure `main` is allowed
   (the default rule "Selected branches: main" is fine). The environment appears after step 4.1.
3. **Actions → job-search → Run workflow → task: dashboard → Run workflow**.
4. Open the run: both jobs **run** and **deploy** should show a green ✓. The **deploy** job shows the link:
   `https://<your-username>.github.io/damir-job-search/`
5. From now on, every run (daily, issue, manual) updates the dashboard automatically.

### 5. Automatic job feed (optional, recommended)
Request a ReliefWeb appname (form at apidoc.reliefweb.int; approval by e-mail). Then **Settings → Secrets and variables → Actions →
Variables → New repository variable** → name `RELIEFWEB_APPNAME`, value = your approved appname.
Until then the daily run shows a notice and everything else works.

## Daily use

1. Open the dashboard → click **Apply** next to a ranked job (form pre-filled), or **Issues → New issue → Apply for a job** and paste any posting.
2. Wait ~1 minute → the issue gets a comment with fit score, gaps and links to all files.
3. Download `cv.docx` and `cover_letter.docx` → replace `[EMAIL]`, `[PHONE]`, every `[EDIT]` and `[VERIFY]` → submit.
4. Add label `status:submitted`. When invited, add `status:interview` and open `interview_prep.md`.
5. Final labels `status:offer` / `status:rejected` / `status:withdrawn` close the issue automatically.

Only issues and labels created by the repository owner trigger the automation; issues from other users are ignored.

To update the CV, edit `profile.json` on GitHub (pencil icon). To add a job source, add an RSS URL to `rss_feeds` in `config.json`.
To re-generate an application, edit the issue text.

## Troubleshooting deployment

| Error in the deploy job | Fix |
|---|---|
| `Get Pages site failed` / `HttpError: Not Found` | Pages is not enabled or source is not GitHub Actions → step 4.1 |
| `Branch "main" is not allowed to deploy to github-pages due to environment protection rules` | Step 4.2: allow `main` in the github-pages environment |
| `Ensure GITHUB_TOKEN has permission "id-token: write"` | You edited the workflow; restore the `permissions` block of the deploy job |
| `No artifacts named "github-pages" were found` | The run job failed earlier; open it and fix the first red step |
| `Resource not accessible by integration` (run job) | Step 2: set Read and write permissions |
| Save results: `push rejected` | Re-run the workflow; the step retries three times automatically |
| *pages build and deployment* still appears with Node 20 warnings | Pages source is still *Deploy from a branch* → step 4.1 |

## Privacy

This repository is public: applications, scores and gaps are visible to anyone. Keep phone, private e-mail and salary floors out of it
(the privacy check enforces this). If you prefer the applications to be confidential, set the repository to **Private** — everything
works the same except that GitHub Pages for private repositories needs a paid plan.

## Maintenance notes

- Only Node.js 24 actions: `actions/checkout@v5`, `actions/upload-artifact@v7`, `actions/deploy-pages@v5`; every job uses `runs-on: ubuntu-24.04`.
- The dashboard is deployed by the job-search workflow; GitHub's built-in Pages workflow is not used.
- GitHub pauses scheduled workflows in public repositories after 60 days without activity; re-enable under **Actions** if needed.

## License
MIT

