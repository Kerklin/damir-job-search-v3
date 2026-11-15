# damir-job-search

A job-search system that runs entirely on **GitHub**: no AI, no LLM, no paid services and no installation on your computer.
Everything is rule-based and traceable to one verified profile (`profile.json`).

Owner: Damir Hadžić, PhD, PMP — Construction Engineer / Project Manager, Sarajevo, BiH.

## What it does

| Function | How it works on GitHub |
|---|---|
| Find jobs | Daily scheduled run fetches ReliefWeb jobs (+ any RSS feeds in `config.json`) |
| Rank jobs | Fit score 1–100: requirements 50 · experience 20 · skill keywords 15 · location 15; hard filters |
| Dashboard | `docs/index.html` on GitHub Pages: ranked jobs, applications funnel, skill gaps, one-click **Apply** |
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
| `.github/workflows/job-search.yml` | The automation (also provided as `WORKFLOW-job-search.yml`) |
| `.github/ISSUE_TEMPLATE/apply.yml` | Apply form (re-created by the setup task if missing) |
| `docs/`, `data/`, `applications/` | Generated output |

## Setup (15 minutes, browser only)

1. **Create the repository** — github.com → **+** → **New repository** → `damir-job-search` → **Public** → no README → **Create**.
2. **Upload** — click **uploading an existing file**, drag the *contents* of the unzipped folder (including the `.github` folder) → **Commit changes**.
   If `.github` did not upload: **Add file → Create new file**, type `.github/workflows/job-search.yml`, paste the content of
   `WORKFLOW-job-search.yml`, **Commit**. (The issue form is created automatically in step 4.)
3. **Permissions** — **Settings → Actions → General → Workflow permissions** → **Read and write permissions** → Save.
4. **Run setup** — **Actions → job-search → Run workflow → task: setup → Run**. Creates the Apply form, labels and dashboard.
5. **Dashboard** — **Settings → Pages → Deploy from a branch → `main` / `/docs`** → Save. Your dashboard:
   `https://<your-username>.github.io/damir-job-search/`
6. **Automatic job feed** — request a ReliefWeb appname (form at apidoc.reliefweb.int; approval by e-mail). Then **Settings → Secrets and
   variables → Actions → Variables → New repository variable** → name `RELIEFWEB_APPNAME`, value = your approved appname.
   Until then the daily run shows a notice and everything else works.

## Daily use

1. Open the dashboard → click **Apply** next to a ranked job (form pre-filled), or **Issues → New issue → Apply for a job** and paste any posting.
2. Wait ~1 minute → the issue gets a comment with fit score, gaps and links to all files.
3. Download `cv.docx` and `cover_letter.docx` → replace `[EMAIL]`, `[PHONE]`, every `[EDIT]` and `[VERIFY]` → submit.
4. Add label `status:submitted`. When invited, add `status:interview` and open `interview_prep.md`.
5. Final labels `status:offer` / `status:rejected` / `status:withdrawn` close the issue automatically.

To update the CV, edit `profile.json` on GitHub (pencil icon). To add a job source, add an RSS URL to `rss_feeds` in `config.json`.
To re-generate an application, edit the issue text.

## Privacy

This repository is public: applications, scores and gaps are visible to anyone. Keep phone, private e-mail and salary floors out of it
(the privacy check enforces this). If you prefer the applications to be confidential, set the repository to **Private** — everything
works the same except that GitHub Pages for private repositories needs a paid plan.

## Maintenance notes

- Workflow uses `actions/checkout@v5` (Node.js 24) and `runs-on: ubuntu-24.04`; no Node 20 actions remain.
- GitHub pauses scheduled workflows in public repositories after 60 days without activity; re-enable under **Actions** if needed.

## License
MIT
