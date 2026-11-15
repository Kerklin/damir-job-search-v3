#!/usr/bin/env python3
"""damir-job-search — rule-based job search engine that runs on GitHub Actions.

No AI/LLM, no external packages: Python 3.10+ standard library only.

Tasks (python3 jobsearch.py <task>):
  ci          dispatcher used by the GitHub workflow (reads the event)
  daily       scrape sources, rank postings, rebuild dashboard
  setup       create issue form + labels, rebuild dashboard
  apply       --file posting.md [--title --org --url --lang en|bs]  (local test)
  dashboard   rebuild docs/index.html, data/tracker.csv, data/upskill.md
  check       privacy check (fails on phone numbers / personal e-mails)
  test        self-test
"""
import argparse, collections, csv, datetime as dt, html, json, os, pathlib, re, subprocess, sys
import urllib.parse, urllib.request, xml.etree.ElementTree as ET, zipfile

ROOT = pathlib.Path(__file__).resolve().parent
DATA, DOCS, APPS = ROOT / "data", ROOT / "docs", ROOT / "applications"
PROFILE = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
BANK = json.loads((ROOT / "interview_bank.json").read_text(encoding="utf-8"))
SKILLS = CFG["skills"]
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TODAY = dt.date.today()
STATUSES = ["drafted", "submitted", "screening", "interview", "offer", "rejected", "withdrawn"]

# ======================================================================= text helpers
STOP = set("""a an the and or of to in for on with by at from as is are be will this that you your our we their they it
its into within across including other such per may must should can all any have has been not who which what where when
experience work working ability strong excellent good knowledge skills years year required preferred desirable
minimum level relevant related etc also well able use using new including position role job candidate applicants
apply application closing date deadline please only staff organization organisation""".split())

NUMWORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "twelve": 12, "fifteen": 15}

def norm(t):
    t = re.sub(r"\s+", " ", (t or "").lower())
    return re.sub(r"\b(" + "|".join(NUMWORDS) + r")\b(?=\s*\(?\d*\)?\s*\+?\s*(?:or more\s*)?years)", lambda m: str(NUMWORDS[m.group(1)]), t)

def slug(t, n=40): return re.sub(r"[^a-z0-9]+", "-", norm(t)).strip("-")[:n] or "job"

def skills_in(text):
    """Canonical skills mentioned in text -> {skill: count}."""
    t = norm(text); out = {}
    for skill, syns in SKILLS.items():
        n = sum(len(re.findall(r"(?<![a-z])" + re.escape(s), t)) for s in syns)
        if n: out[skill] = n
    return out

def profile_text(training=False):
    p = PROFILE
    if training:  # education + certifications = learning evidence, not work experience
        return "\n".join(p["education"] + p["certifications"])
    parts = [p["summary"], " ".join(p["core_competencies"]), p["skills_tools"], p["earlier_experience"]]
    for e in p["experience"]: parts += [e["title"], e["org"]] + e["bullets"]
    return "\n".join(parts)

PTEXT = profile_text()
PSKILLS = set(skills_in(PTEXT)) | set(CFG.get("profile_skills_override", []))   # proven by experience
TSKILLS = set(skills_in(profile_text(True))) - PSKILLS                          # training / degree only
GENERIC = set(CFG.get("generic_skills", []))

def top_terms(text, n=20):
    w = [x for x in re.findall(r"[a-z][a-z/\-]+", norm(text)) if x not in STOP and len(x) > 2]
    bi = collections.Counter(" ".join(p) for p in zip(w, w[1:]) if not set(p) & STOP)
    c = collections.Counter(w) + collections.Counter({b: k * 2 for b, k in bi.items() if k > 1})
    return [k for k, _ in c.most_common(n)]

# ======================================================================= requirement analysis
REQ_CUE = re.compile(r"(required|requirement|must|minimum|at least|years|degree|experience (in|with|of)|knowledge of|"
                     r"proficien|fluen|familiar|certif|qualification|ability to|skills in|desirable|asset|advantage|preferred)", re.I)
DESIRABLE = re.compile(r"(desirable|an asset|advantage|preferred|nice to have)", re.I)
LANGS = ["english", "french", "spanish", "arabic", "russian", "chinese", "portuguese", "german", "italian",
         "bosnian", "croatian", "serbian", "albanian", "macedonian", "turkish", "ukrainian"]
LEVEL = {"native": 3, "fluent": 3, "working": 2, "basic": 1}

def extract_requirements(text, limit=30):
    reqs, desirable_zone = [], False
    for raw in re.split(r"\n+|(?<=[.;])\s+(?=[A-Z•\-])", text or ""):
        line = raw.strip(" •-*\t")
        if not line: continue
        if line.endswith(":") and len(line) < 80:
            if re.search(r"(desirable|asset|preferred)", line, re.I): desirable_zone = True
            elif re.search(r"(required|essential|minimum|qualification)", line, re.I): desirable_zone = False
            continue
        if len(line) < 60 and re.search(r"(desirable|asset|preferred)", line, re.I): desirable_zone = True
        if len(line) < 60 and re.search(r"(required|essential|minimum|qualifications|competencies)", line, re.I) and not DESIRABLE.search(line):
            desirable_zone = False
        if 15 <= len(line) <= 450 and REQ_CUE.search(line):
            reqs.append({"text": line, "desirable": desirable_zone or bool(DESIRABLE.search(line))})
    seen, out = set(), []
    for r in reqs:
        k = norm(r["text"])[:80]
        if k not in seen: seen.add(k); out.append(r)
    return out[:limit]

def best_evidence(req_skills, req_text=""):
    best, score = None, 0
    n = len(PROFILE["experience"])
    for idx, e in enumerate(PROFILE["experience"]):
        for b in e["bullets"]:
            s = len(set(skills_in(b)) & req_skills) * 3 + len(set(top_terms(b, 30)) & set(top_terms(req_text, 30))) + (n - idx) * 1.0
            if s > score: best, score = f"{b} ({e['org']}, {e['start'][-4:]}–{e['end'][-4:]})", s
    return best

def evaluate(req):
    t, checks = norm(req["text"]), []
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:or more\s*)?years", t)
    if m:
        need = int(m.group(1)); checks.append("met" if PROFILE["years_experience"] >= need else "gap")
    degree = re.search(r"(advanced university degree|master|bachelor|university degree|phd|doctorate|first.level)", t)
    if degree:
        field_ok = any(w in t for w in ["architect", "engineering", "construction", "built environment", "project management"]) or \
                   not re.search(r"(in|of)\s+(civil|mechanical|electrical|economics|law|finance|social|public health)", t)
        checks.append("met" if field_ok else "partly")
    langs = [l for l in LANGS if l in t]
    if langs:
        known = CFG.get("languages_known", {})
        lv = [LEVEL.get(known.get(l, ""), 0) for l in langs]
        need_fluent = bool(re.search(r"(fluen|excellent|full professional|native)", t))
        if all(v >= (3 if need_fluent else 2) for v in lv): checks.append("met")
        elif any(v >= 2 for v in lv): checks.append("partly")
        else: checks.append("gap")
    rs = set(skills_in(req["text"]))
    check_skills = rs - ({"civil engineering", "architecture"} if degree else set())
    if check_skills:
        have, trained = check_skills & PSKILLS, check_skills & TSKILLS
        checks.append("met" if have == check_skills else "partly" if (have or trained) else "gap")
    ev = []
    if m: ev.append(f"{PROFILE['years_experience']}+ years of professional experience")
    if degree: ev.append(PROFILE["education"][0])
    if langs: ev.append("Languages: " + ", ".join(f"{k} {v}" for k, v in PROFILE["languages"].items()))
    if check_skills:
        if check_skills & PSKILLS:
            b = best_evidence(check_skills & PSKILLS, req["text"])
            if b: ev.append(b)
        elif check_skills & TSKILLS:
            ev.append("Training only: " + "; ".join(c for c in PROFILE["certifications"] + PROFILE["education"]
                                                     if set(skills_in(c)) & check_skills)[:200])
    if not checks: status = "review"
    elif all(c == "met" for c in checks): status = "met"
    elif all(c == "gap" for c in checks): status = "gap"
    else: status = "partly"
    return {**req, "status": status, "skills": sorted(rs), "missing": sorted(check_skills - PSKILLS),
            "evidence": " · ".join(ev) if status in ("met", "partly") and ev else None,
            "skill_based": bool(check_skills & PSKILLS)}

def location_ok(text):
    t = norm(text); return any(p in t for p in CFG["ranking"]["preferred_locations"])

def analyse(posting, location_hint=""):
    rk = CFG["ranking"]; w = rk["weights"]
    excluded = [x for x in rk["exclude_if_contains"] if x in norm(posting)]
    reqs = [evaluate(r) for r in extract_requirements(posting)]
    scored = [r for r in reqs if r["status"] != "review"]
    psk = skills_in(posting)
    if scored:
        req_ratio = sum(1 if r["status"] == "met" else .5 if r["status"] == "partly" else 0 for r in scored) / len(scored)
    else:
        req_ratio = len(set(psk) & PSKILLS) / max(len(psk), 1)
    years = [int(x) for x in re.findall(r"(\d{1,2})\s*\+?\s*(?:or more\s*)?years", norm(posting)) if int(x) < 40]
    exp_ratio = min(1.0, PROFILE["years_experience"] / max(years)) if years else 1.0
    kw_ratio = len(set(psk) & PSKILLS) / max(len(psk), 1)
    loc_ratio = 1.0 if location_ok(location_hint + " " + posting[:1500]) else 0.5
    score = round(w["requirements"] * req_ratio + w["experience"] * exp_ratio + w["keywords"] * kw_ratio + w["location"] * loc_ratio)
    if excluded: score = 0
    rec = "Skip (excluded: " + ", ".join(excluded) + ")" if excluded else "Apply" if score >= 70 else "Apply with framing" if score >= 50 else "Skip"
    gaps = sorted({s for r in reqs for s in r["missing"]} | (set(psk) - PSKILLS - {"civil engineering", "architecture"}))
    return {"score": score, "recommendation": rec, "requirements": reqs, "posting_skills": psk, "gaps": gaps,
            "matches": sorted(set(psk) & PSKILLS, key=lambda s: -psk[s])}

# ======================================================================= document builders
def tailor_bullets(psk):
    out = []
    for e in PROFILE["experience"]:
        ranked = sorted(enumerate(e["bullets"]), key=lambda ib: (-sum(psk.get(s, 0) for s in skills_in(ib[1])), ib[0]))
        keep = sorted(ranked[: e.get("max_bullets", 3)], key=lambda ib: ib[0]) if e is PROFILE["experience"][0] else ranked[: e.get("max_bullets", 3)]
        out.append({**e, "bullets": [b for _, b in keep]})
    return out

def pick_headline(psk):
    return max(PROFILE["headlines"], key=lambda h: sum(psk.get(s, 0) for s in skills_in(h)))

def competencies(psk, n=16):
    comp = sorted(PROFILE["core_competencies"], key=lambda c: -sum(psk.get(s, 0) for s in skills_in(c)))
    extra = [s.title() for s in sorted(set(psk) & PSKILLS, key=lambda s: -psk[s])
             if not any(s in norm(c) for c in comp)]
    return (extra[:4] + comp)[:n]

H = {"en": ["PROFESSIONAL SUMMARY", "CORE COMPETENCIES", "PROFESSIONAL EXPERIENCE", "EDUCATION", "CERTIFICATIONS",
            "LANGUAGES", "ADDITIONAL INFORMATION", "Earlier experience", "Driving licence", "Selected publications"],
     "bs": ["PROFESIONALNI SAŽETAK", "KLJUČNE KOMPETENCIJE", "RADNO ISKUSTVO", "OBRAZOVANJE", "CERTIFIKATI",
            "JEZICI", "DODATNE INFORMACIJE", "Ranije iskustvo", "Vozačka dozvola", "Odabrane publikacije"]}

def build_cv(a, lang="en"):
    h, p, psk = H[lang], PROFILE, a["posting_skills"]
    top = [s for s in a["matches"] if s not in GENERIC][:4]
    summary = p["summary"] + (f" Relevant strengths for this role: {', '.join(top)}." if top else "")
    L = [f"# {p['name']}", pick_headline(psk), f"{p['location']} | [EMAIL] | [PHONE] | {p['linkedin']}", "",
         f"## {h[0]}", summary, "", f"## {h[1]}", " | ".join(competencies(psk)), "", f"## {h[2]}"]
    for e in tailor_bullets(psk):
        L += ["", f"### {e['title']} | {e['org']}, {e['city']} | {e['start']} – {e['end']}"] + [f"- {b}" for b in e["bullets"]]
    L += ["", f"{h[7]}: {p['earlier_experience']}", "", f"## {h[3]}"] + [f"- {x}" for x in p["education"]]
    L += ["", f"## {h[4]}"] + [f"- {x}" for x in p["certifications"]]
    L += ["", f"## {h[5]}", " | ".join(f"{k}: {v}" for k, v in p["languages"].items())]
    L += ["", f"## {h[6]}", f"- {h[8]}: {p['driving']}", f"- Tools: {p['skills_tools']}", f"- {h[9]}: " + " ".join(p["publications"])]
    if lang == "bs": L.insert(3, "> Napomena: tekst iskustva je na engleskom — prevedite prije slanja.")
    return "\n".join(L) + "\n"

def template(name):
    t = (ROOT / "templates.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {name}\n(.*?)(?=^## |\Z)", t, re.S | re.M)
    return m.group(1).strip() if m else ""

def build_cover(a, meta, lang="en"):
    ranked = sorted([r for r in a["requirements"] if r["evidence"] and r.get("skill_based")], key=lambda r: (r["desirable"], r["status"] != "met"))
    lines, used = [], set()
    for r in ranked[:3]:
        b = r["evidence"].split(" · ")[-1].split(" (")[0]; used.add(b)
        lines.append(f"- {r['text'][:160].rstrip('.')}: {b}")
    for s in [s for s in a["matches"] if s not in GENERIC]:
        if len(lines) >= 3: break
        b = (best_evidence({s}) or "").split(" (")[0]
        if b and b not in used: used.add(b); lines.append(f"- {s.upper() if len(s) <= 4 else s.capitalize()}: {b}")
    ev = "\n".join(lines) or "- [ADD 2–3 evidence points]"
    org = meta.get("org") or "your organisation"
    motiv = "[EDIT: one or two specific sentences on why this organisation and programme — mandate, country programme, project.]"
    for key, txt in CFG.get("org_motivation", {}).items():
        if key.lower() in norm(org): motiv = txt
    langs = ", ".join(k for k, v in PROFILE["languages"].items() if v in ("Native", "Fluent"))
    return template("COVER_LETTER_BS" if lang == "bs" else "COVER_LETTER_EN").format(
        name=PROFILE["name"], short_name=PROFILE["name"].split(",")[0], location=PROFILE["location"], linkedin=PROFILE["linkedin"],
        date=TODAY.strftime("%d %B %Y"), org=org, title=meta.get("title") or "the advertised position",
        ref=f" ({meta['ref']})" if meta.get("ref") else "", evidence=ev, motivation=motiv, years=PROFILE["years_experience"],
        languages=langs, top_skills=", ".join([s for s in a["matches"] if s not in GENERIC][:3]) or "its programme") + "\n"

def build_screening(a, meta):
    L = [f"# HR screening — {meta.get('title','')} | {meta.get('org','')}", "",
         f"**Fit score: {a['score']}/100 — {a['recommendation']}**", "",
         "| # | Requirement | Type | Status | Evidence |", "|---|---|---|---|---|"]
    icon = {"met": "✅ Met", "partly": "🟡 Partly", "gap": "❌ Gap", "review": "🔎 Review manually"}
    for i, r in enumerate(a["requirements"], 1):
        ev = (r["evidence"] or (("missing: " + ", ".join(r["missing"])) if r["missing"] else "")).replace("|", "/")
        L.append(f"| {i} | {r['text'][:180].replace('|','/')} | {'Desirable' if r['desirable'] else 'Required'} | {icon[r['status']]} | {ev[:220]} |")
    L += ["", f"**Matched skills:** {', '.join(a['matches']) or '—'}", f"**Gaps:** {', '.join(a['gaps']) or '—'}"]
    return "\n".join(L) + "\n"

def build_ats(a, cv_md):
    cvs = set(skills_in(cv_md)); psk = a["posting_skills"]
    L = ["# ATS keyword report", "", "| Skill keyword (posting count) | In CV | Where to place if missing |", "|---|---|---|"]
    hit = 0
    for s, n in sorted(psk.items(), key=lambda x: -x[1]):
        ok = s in cvs; hit += ok
        L.append(f"| {s} ({n}) | {'✅' if ok else '❌'} | {'' if ok else ('Core Competencies + one bullet' if s in PSKILLS else 'Not in profile — do not claim; address as learning')} |")
    cov = round(100 * hit / max(len(psk), 1))
    L.insert(2, f"Skill coverage: **{cov}%**  ·  Exact-term check of top posting terms below.\n")
    cvn = norm(cv_md)
    L += ["", "| Top posting term | In CV |", "|---|---|"] + [f"| {t} | {'✅' if t in cvn else '❌'} |" for t in a.get("terms", [])]
    return "\n".join(L) + "\n", cov

def build_form_answers(a, meta, cv_md):
    p = PROFILE
    L = [f"# Copy-paste blocks for online application forms — {meta.get('title','')}", "", "## Summary / profile",
         re.search(r"## [^\n]+\n(.+?)\n", cv_md).group(1), "", "## Motivation (edit before use)",
         f"I am applying for {meta.get('title','this role')} because it matches my experience in {', '.join(a['matches'][:4]) or 'construction and rehabilitation'}. "
         "[EDIT: add one specific sentence about the organisation's programme.]", "", "## Experience entries"]
    for e in tailor_bullets(a["posting_skills"]):
        L += ["", f"Title: {e['title']}", f"Employer: {e['org']}", f"Location: {e['city']}, Bosnia and Herzegovina",
              f"From: {e['start']}  To: {e['end']}", "Duties and achievements:"] + [f"• {b}" for b in e["bullets"]]
    L += ["", "## Languages"] + [f"{k}: {v}" for k, v in p["languages"].items()]
    return "\n".join(L) + "\n"

def build_salary(posting, meta):
    t = posting + " " + meta.get("grade", "")
    grade = re.search(r"\b(P-?\d|D-?\d|NO-?[A-D]|NO[A-D]|G-?\d|GS-?\d|SB-?\d|LICA-?\d|IICA-?\d)\b", t, re.I)
    consult = re.search(r"(consultan|daily rate|individual contractor|fee)", t, re.I)
    L = ["# Salary check", "", f"- Detected grade/contract: **{grade.group(1).upper() if grade else ('Consultancy' if consult else 'not stated')}**",
         f"- Duty station: {meta.get('duty_station') or '[check posting]'}", "", "## Where to look (official sources)"]
    L += [f"- {k}: {v}" for k, v in CFG["salary_links"].items()]
    L += ["", "## Steps", "1. UN staff posts: find the grade in the salary scale; for P/D grades add post adjustment for the duty station.",
          "2. National Officer / General Service: use the local salary scale for Bosnia and Herzegovina (or the duty station).",
          "3. Consultancies: compare with your private daily-rate floor (kept offline, not in this repository).",
          "4. Private sector: compare at least two local salary surveys; record the source and date.",
          "", "No figures are generated automatically, to avoid outdated or invented numbers."]
    return "\n".join(L) + "\n"

def build_interview(a, posting, meta):
    t = norm(posting); comps = []
    for name, c in BANK["competencies"].items():
        n = sum(t.count(x) for x in c["triggers"])
        if n: comps.append((n, name, c))
    comps.sort(key=lambda x: -x[0])
    used = collections.Counter()
    def star_for(tags):
        tags = set(tags)
        s = max(PROFILE["star"], key=lambda s: len(tags & set(s["tags"])) * 3 + len(set(a["matches"]) & set(s["tags"])) - used[s["id"]] * 4)
        used[s["id"]] += 1; return s
    L = [f"# Interview preparation — {meta.get('title','')} | {meta.get('org','')}", "",
         "Speak each answer in 60–90 seconds. Replace every [VERIFY] with a confirmed fact before the interview.", ""]
    q = 0
    for _, name, c in comps[:6]:
        L.append(f"## Competency: {name}")
        for question in c["questions"]:
            q += 1; s = star_for(c["tags"])
            L += [f"**Q{q}. {question}**", f"Example {s['id']} — {s['title']}",
                  f"- Situation: {s['s']}", f"- Task: {s['t']}", f"- Action: {s['a']}", f"- Result: {s['r']}", ""]
    L.append("## Technical questions")
    for sk in a["matches"][:4]:
        q += 1; s = star_for([sk])
        L += [f"**Q{q}. How do you apply {sk} on a multi-site rehabilitation programme?**", f"Use example {s['id']} ({s['title']}): {s['a']}", ""]
    L.append("## Gap questions — truthful bridge answers")
    for g in a["gaps"][:4]:
        res = CFG["learning_resources"].get(g, CFG["learning_resources"]["default"])
        L += [f"- **{g}**: \"I have not led {g} as a main responsibility. The closest experience is [closest example]. "
              f"I am closing the gap through {res}.\""]
    L += ["", "## Motivation"] + [f"- {m}" for m in BANK["motivation"]]
    L += ["", "## Questions to ask the panel"] + [f"- {x}" for x in CFG["panel_questions"]]
    return "\n".join(L) + "\n"

# ----------------------------------------------------------------------- minimal DOCX writer (stdlib)
def md_to_docx(md, path):
    def runs(text, size=21, bold=False):
        out = []
        for i, part in enumerate(re.split(r"\*\*", text)):
            if not part: continue
            b = "<w:b/>" if bold or i % 2 else ""
            out.append(f'<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>{b}<w:sz w:val="{size}"/></w:rPr>'
                       f'<w:t xml:space="preserve">{html.escape(part, quote=False)}</w:t></w:r>')
        return "".join(out)
    paras = []
    for line in md.splitlines():
        if not line.strip(): continue
        if line.startswith("> "): line = line[2:]
        if line.startswith("# "): paras.append(f'<w:p><w:pPr><w:spacing w:after="40"/></w:pPr>{runs(line[2:], 32, True)}</w:p>')
        elif line.startswith("## "): paras.append(f'<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="1F4E79"/></w:pBdr><w:spacing w:before="200" w:after="60"/></w:pPr>{runs(line[3:], 24, True)}</w:p>')
        elif line.startswith("### "): paras.append(f'<w:p><w:pPr><w:spacing w:before="120" w:after="40"/></w:pPr>{runs(line[4:], 21, True)}</w:p>')
        elif line.startswith(("- ", "• ")): paras.append(f'<w:p><w:pPr><w:ind w:left="360" w:hanging="220"/><w:spacing w:after="30"/></w:pPr>{runs("• " + line[2:])}</w:p>')
        elif line.startswith("|"): paras.append(f'<w:p>{runs(line.replace("|", "  ").strip(), 18)}</w:p>')
        else: paras.append(f'<w:p><w:pPr><w:spacing w:after="60"/></w:pPr>{runs(line)}</w:p>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(paras) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1000" w:right="1000" w:bottom="1000" w:left="1000" w:header="500" w:footer="500" w:gutter="0"/></w:sectPr></w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)

# ======================================================================= apply / interview pipelines
def run_apply(posting, meta, folder):
    folder.mkdir(parents=True, exist_ok=True)
    lang = "bs" if (meta.get("lang") or "").lower().startswith(("bs", "bos")) else "en"
    a = analyse(posting, meta.get("duty_station", "")); a["terms"] = top_terms(posting, 20)
    cv = build_cv(a, lang); cover = build_cover(a, meta, lang)
    ats, cov = build_ats(a, cv)
    files = {"job.md": f"# {meta.get('title','')} — {meta.get('org','')}\n\nURL: {meta.get('url','')}\n\n{posting}\n",
             "screening.md": build_screening(a, meta), "cv.md": cv, "cover_letter.md": cover, "ats_report.md": ats,
             "form_answers.md": build_form_answers(a, meta, cv), "salary.md": build_salary(posting, meta)}
    for n, c in files.items(): (folder / n).write_text(c, encoding="utf-8")
    md_to_docx(cv, folder / "cv.docx"); md_to_docx(cover, folder / "cover_letter.docx")
    summary = {"title": meta.get("title"), "org": meta.get("org"), "url": meta.get("url"), "issue": meta.get("issue"),
               "score": a["score"], "recommendation": a["recommendation"], "ats_coverage": cov, "gaps": a["gaps"],
               "matches": a["matches"], "lang": lang, "date": TODAY.isoformat()}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return a, summary

def run_interview(folder):
    posting = (folder / "job.md").read_text(encoding="utf-8")
    meta = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    a = analyse(posting)
    (folder / "interview_prep.md").write_text(build_interview(a, posting, meta), encoding="utf-8")

# ======================================================================= GitHub integration
def gh(*args, input_text=None):
    if not os.environ.get("GH_TOKEN"): return None
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=input_text)
    if r.returncode: print("gh error:", r.stderr.strip()[:300])
    return r.stdout

def parse_issue_form(body):
    fields = {}
    for m in re.finditer(r"^### (.+?)\n\n(.*?)(?=^### |\Z)", body or "", re.S | re.M):
        v = m.group(2).strip(); fields[m.group(1).strip().lower()] = "" if v == "_No response_" else v
    return fields

def issue_folder(num): 
    hits = sorted(APPS.glob(f"{num}-*")) if APPS.exists() else []
    return hits[0] if hits else None

def handle_issue(ev):
    issue, action = ev["issue"], ev.get("action")
    labels = [l["name"] for l in issue.get("labels", [])]
    num = issue["number"]
    if "apply" not in labels: return print("issue without 'apply' label — ignored")
    added = (ev.get("label") or {}).get("name", "")
    if action in ("opened", "edited") or added == "apply":
        f = parse_issue_form(issue.get("body", ""))
        posting = f.get("job posting text", "") or issue.get("body", "")
        meta = {"title": f.get("job title") or issue["title"].replace("[APPLY]", "").strip(), "org": f.get("organisation", ""),
                "url": f.get("posting url", ""), "lang": f.get("output language", "English"), "grade": f.get("grade or contract type", ""),
                "duty_station": f.get("duty station", ""), "ref": f.get("vacancy reference", ""), "issue": num}
        old = issue_folder(num)
        folder = old or APPS / f"{num}-{slug(meta['org'] + ' ' + meta['title'])}"
        a, s = run_apply(posting, meta, folder)
        if not any(l.startswith("status:") for l in labels): gh("issue", "edit", str(num), "--add-label", "status:drafted")
        base = f"https://github.com/{REPO}/blob/main/{folder.relative_to(ROOT).as_posix()}"
        body = (f"## Fit score: {s['score']}/100 — {s['recommendation']}\nATS skill coverage: **{s['ats_coverage']}%**\n\n"
                f"**Matched:** {', '.join(s['matches']) or '—'}\n\n**Gaps:** {', '.join(s['gaps']) or '—'}\n\n"
                f"### Files\n- [CV (Word)]({base}/cv.docx) · [CV (text)]({base}/cv.md)\n- [Cover letter (Word)]({base}/cover_letter.docx) · [text]({base}/cover_letter.md)\n"
                f"- [HR screening]({base}/screening.md) · [ATS report]({base}/ats_report.md)\n- [Form answers]({base}/form_answers.md) · [Salary check]({base}/salary.md)\n\n"
                "Next: replace [EMAIL]/[PHONE] and every [EDIT]/[VERIFY] in Word, submit, then add the label `status:submitted`.\n"
                "Add `status:interview` when invited — interview preparation is generated automatically.")
        gh("issue", "comment", str(num), "--body-file", "-", input_text=body)
    if added.startswith("status:"):
        for other in [l for l in labels if l.startswith("status:") and l != added]:
            gh("issue", "edit", str(num), "--remove-label", other)
        if added == "status:interview" and issue_folder(num):
            run_interview(issue_folder(num))
            gh("issue", "comment", str(num), "--body-file", "-", input_text=
               f"Interview preparation ready: https://github.com/{REPO}/blob/main/{issue_folder(num).relative_to(ROOT).as_posix()}/interview_prep.md")
        if added in ("status:offer", "status:rejected", "status:withdrawn"): gh("issue", "close", str(num))
    build_dashboard()

ISSUE_FORM = """name: Apply for a job
description: Paste a job posting to generate screening, tailored CV, cover letter and more
title: "[APPLY] "
labels: ["apply"]
body:
  - type: input
    id: job_title
    attributes: {label: Job title}
    validations: {required: true}
  - type: input
    id: organisation
    attributes: {label: Organisation}
    validations: {required: true}
  - type: input
    id: posting_url
    attributes: {label: Posting URL}
  - type: input
    id: vacancy_ref
    attributes: {label: Vacancy reference}
  - type: input
    id: grade
    attributes: {label: Grade or contract type, description: "e.g. P-3, NO-C, consultancy"}
  - type: input
    id: duty_station
    attributes: {label: Duty station}
  - type: dropdown
    id: language
    attributes: {label: Output language, options: [English, Bosnian]}
  - type: textarea
    id: posting_text
    attributes: {label: Job posting text, description: "Paste the full posting: responsibilities, qualifications, competencies"}
    validations: {required: true}
"""
LABELS = {"apply": "1f6feb", "status:drafted": "c5def5", "status:submitted": "0e8a16", "status:screening": "fbca04",
          "status:interview": "d93f0b", "status:offer": "5319e7", "status:rejected": "b60205", "status:withdrawn": "999999"}

def cmd_setup(_=None):
    d = ROOT / ".github" / "ISSUE_TEMPLATE"; d.mkdir(parents=True, exist_ok=True)
    (d / "apply.yml").write_text(ISSUE_FORM, encoding="utf-8")
    (d / "config.yml").write_text("blank_issues_enabled: true\n", encoding="utf-8")
    for n, c in LABELS.items(): gh("label", "create", n, "--color", c, "--force")
    build_dashboard(); print("setup done: issue form + labels + dashboard")

# ======================================================================= sources
def http(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "damir-job-search/3.0", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r: return r.read()

def strip_html(t): return re.sub(r"\s+\n", "\n", re.sub(r"<[^>]+>", "\n", html.unescape(t or "")))

def from_reliefweb():
    c = CFG["sources"]["reliefweb"]; app = os.environ.get("RELIEFWEB_APPNAME", "").strip()
    if not c.get("enabled"): return []
    if not app:
        print("::notice::ReliefWeb skipped — add repository variable RELIEFWEB_APPNAME (approved appname from apidoc.reliefweb.int)."); return []
    out, url = [], "https://api.reliefweb.int/v2/jobs?appname=" + urllib.parse.quote(app)
    for q in c["queries"]:
        body = {"query": {"value": q}, "limit": c.get("limit", 40), "sort": ["date.created:desc"],
                "fields": {"include": ["title", "body", "source.name", "country.name", "city.name", "date.closing", "url"]}}
        try: data = json.loads(http(url, json.dumps(body).encode()))
        except Exception as e:
            print(f"::warning::ReliefWeb '{q}' failed: {e}" + (" — appname not approved?" if "403" in str(e) else "")); continue
        for it in data.get("data", []):
            f = it.get("fields", {})
            loc = ", ".join([x.get("name", "") for x in f.get("city", [])] + [x.get("name", "") for x in f.get("country", [])])
            out.append({"title": f.get("title", ""), "org": (f.get("source") or [{}])[0].get("name", ""), "location": loc or "Multiple/Home-based",
                        "closing": ((f.get("date") or {}).get("closing") or "")[:10], "url": f.get("url", ""), "source": "ReliefWeb",
                        "text": strip_html(f.get("body", ""))[:8000]})
    return out

def from_rss():
    out = []
    for feed in CFG["sources"].get("rss_feeds", []):
        try: root = ET.fromstring(http(feed))
        except Exception as e: print(f"::warning::RSS {feed} failed: {e}"); continue
        for it in root.iter("item"):
            out.append({"title": it.findtext("title") or "", "org": "", "location": "", "closing": "", "url": it.findtext("link") or "",
                        "source": urllib.parse.urlparse(feed).netloc, "text": strip_html(it.findtext("description"))[:8000]})
    return out

def cmd_daily(_=None):
    DATA.mkdir(exist_ok=True); store_f = DATA / "jobs.json"
    store = json.loads(store_f.read_text(encoding="utf-8")) if store_f.exists() else {}
    new = 0
    for j in from_reliefweb() + from_rss():
        k = re.sub(r"\W", "", (j["url"] or j["title"] + j["org"]).lower())[-80:]
        if k in store: continue
        a = analyse(j["title"] + "\n" + j["text"], j["location"])
        store[k] = {**{x: j[x] for x in ("title", "org", "location", "closing", "url", "source")}, "found": TODAY.isoformat(),
                    "score": a["score"], "recommendation": a["recommendation"], "matches": a["matches"][:5], "gaps": a["gaps"][:5],
                    "text": j["text"][:3000]}
        new += 1
    keep = CFG["ranking"]["keep_days"]
    for k in list(store):
        j = store[k]
        expired = j.get("closing") and j["closing"] < TODAY.isoformat()
        old = (TODAY - dt.date.fromisoformat(j["found"])).days > keep
        if expired or old: del store[k]
    store_f.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"new={new} total={len(store)}"); build_dashboard()

# ======================================================================= dashboard, tracker, upskill
def tracker_rows():
    rows = []
    out = gh("issue", "list", "--label", "apply", "--state", "all", "--limit", "500", "--json", "number,title,labels,createdAt,url")
    issues = json.loads(out) if out else []
    for i in issues:
        st = [l["name"][7:] for l in i["labels"] if l["name"].startswith("status:")]
        st = max(st, key=STATUSES.index) if st else "drafted"
        f = issue_folder(i["number"]); s = json.loads((f / "summary.json").read_text(encoding="utf-8")) if f else {}
        rows.append({"issue": i["number"], "date": i["createdAt"][:10], "org": s.get("org", ""), "role": s.get("title") or i["title"],
                     "status": st, "score": s.get("score", ""), "url": i["url"]})
    if not issues and APPS.exists():
        for f in sorted(APPS.glob("*/summary.json")):
            s = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"issue": s.get("issue", ""), "date": s["date"], "org": s["org"], "role": s["title"], "status": "drafted", "score": s["score"], "url": ""})
    return rows

def gap_counts(jobs):
    c = collections.Counter()
    for j in jobs.values(): c.update(j.get("gaps", []))
    if APPS.exists():
        for f in APPS.glob("*/summary.json"): c.update({g: 3 for g in json.loads(f.read_text(encoding="utf-8")).get("gaps", [])})
    return c

def build_dashboard():
    DATA.mkdir(exist_ok=True); DOCS.mkdir(exist_ok=True)
    jf = DATA / "jobs.json"; jobs = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
    rows = tracker_rows()
    with (DATA / "tracker.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["issue", "date", "org", "role", "status", "score", "url"]); w.writeheader(); w.writerows(rows)
    gaps = gap_counts(jobs)
    up = ["# Upskill plan (auto-generated)", "", "| Gap | Weight | Suggested learning |", "|---|---|---|"]
    up += [f"| {g} | {n} | {CFG['learning_resources'].get(g, CFG['learning_resources']['default'])} |" for g, n in gaps.most_common(10)]
    (DATA / "upskill.md").write_text("\n".join(up) + "\n", encoding="utf-8")
    ranked = sorted(jobs.values(), key=lambda j: -j["score"])
    (DATA / "ranked.md").write_text("# Ranked postings\n\n| Score | Title | Org | Location | Closing | Advice |\n|---|---|---|---|---|---|\n" +
        "\n".join(f"| {j['score']} | [{j['title']}]({j['url']}) | {j['org']} | {j['location']} | {j['closing']} | {j['recommendation']} |" for j in ranked) + "\n", encoding="utf-8")
    e = lambda x: html.escape(str(x or ""))
    def apply_link(j):
        q = {"template": "apply.yml", "title": f"[APPLY] {j['title']}"[:120], "job_title": j["title"], "organisation": j["org"],
             "posting_url": j["url"], "duty_station": j["location"], "posting_text": j.get("text", "")[:2500]}
        return f"https://github.com/{REPO}/issues/new?" + urllib.parse.urlencode(q) if REPO else "#"
    funnel = collections.Counter(r["status"] for r in rows)
    minsc = CFG["ranking"]["min_score_dashboard"]
    jt = "".join(f"<tr><td class=s{min(j['score']//25,3)}>{j['score']}</td><td><a href='{e(j['url'])}'>{e(j['title'])}</a></td><td>{e(j['org'])}</td>"
                 f"<td>{e(j['location'])}</td><td>{e(j['closing'])}</td><td>{e(j['recommendation'])}</td><td>{e(', '.join(j['gaps'][:3]))}</td>"
                 f"<td><a class=btn href='{e(apply_link(j))}'>Apply</a></td></tr>" for j in ranked if j["score"] >= minsc)
    at = "".join(f"<tr><td><a href='{e(r['url'])}'>#{e(r['issue'])}</a></td><td>{e(r['date'])}</td><td>{e(r['org'])}</td><td>{e(r['role'])}</td>"
                 f"<td>{e(r['status'])}</td><td>{e(r['score'])}</td></tr>" for r in rows)
    gt = "".join(f"<tr><td>{e(g)}</td><td><div class=bar style='width:{min(n*12,300)}px'></div> {n}</td><td>{e(CFG['learning_resources'].get(g, CFG['learning_resources']['default']))}</td></tr>" for g, n in gaps.most_common(10))
    fn = " · ".join(f"<b>{s}</b> {funnel.get(s,0)}" for s in STATUSES)
    new_issue = f"https://github.com/{REPO}/issues/new?template=apply.yml" if REPO else "#"
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Job search dashboard</title><style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b1f23}}h1{{color:#1F4E79}}
table{{border-collapse:collapse;width:100%;margin:8px 0 28px;font-size:14px}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}}
th{{background:#f3f6fa}}.s3{{background:#c8f0d0}}.s2{{background:#fff3c4}}.s1,.s0{{background:#fde0e0}}.bar{{display:inline-block;height:10px;background:#1F4E79}}
.btn{{background:#1f6feb;color:#fff;padding:3px 8px;border-radius:4px;text-decoration:none}}</style></head><body>
<h1>Job search dashboard</h1><p>Updated {TODAY.isoformat()} · <a class=btn href='{new_issue}'>+ New application</a></p>
<h2>Applications</h2><p>{fn}</p><table><tr><th>Issue</th><th>Date</th><th>Organisation</th><th>Role</th><th>Status</th><th>Fit</th></tr>{at or '<tr><td colspan=6>No applications yet</td></tr>'}</table>
<h2>Ranked postings ({len(ranked)})</h2><table><tr><th>Fit</th><th>Title</th><th>Organisation</th><th>Location</th><th>Closing</th><th>Advice</th><th>Gaps</th><th></th></tr>{jt or '<tr><td colspan=8>No postings yet — see README, step 4</td></tr>'}</table>
<h2>Skill gaps &amp; learning plan</h2><table><tr><th>Gap</th><th>Weight</th><th>Suggested learning</th></tr>{gt or '<tr><td colspan=3>No gaps recorded</td></tr>'}</table>
</body></html>"""
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    for d in (DATA, DOCS, APPS): d.mkdir(exist_ok=True); (d / ".gitkeep").touch()
    print("dashboard rebuilt")

# ======================================================================= privacy check + tests
def _pat():
    return [re.compile(r"\+?387[\s\-/]?6\d[\s\-/]?\d{3}[\s\-/]?\d{3}"),
            re.compile(r"[\w.\-]+@(gmail|yahoo|hotmail|outlook|icloud)\.com", re.I),
            re.compile(r"[\w.\-]+@" + "unicef" + r"\.org", re.I)]

def find_private(text): return [m.group(0) for p in _pat() for m in p.finditer(text)]

def cmd_check(_=None):
    bad = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        if not p.is_file() or rel.startswith((".git/", "data/", "docs/")) or p.suffix not in {".md", ".json", ".yml", ".yaml", ".txt", ".csv", ".html"}: continue
        if rel.startswith("applications/") and p.name == "job.md": continue
        bad += [f"{rel}: {h}" for h in find_private(p.read_text(encoding="utf-8", errors="ignore"))]
    if bad: print("::error::Private data found:\n" + "\n".join(bad)); sys.exit(1)
    print("privacy check passed")

SAMPLE = """Construction Engineer, NO-C, Sarajevo, Bosnia and Herzegovina
Responsibilities: supervise rehabilitation works, quality assurance, review bills of quantities, contract management, reporting.
Minimum requirements:
- Advanced university degree in civil engineering, architecture or related field.
- At least 7 years of experience in construction supervision and contract management.
- Experience with procurement of works and tender evaluation is required.
- Fluency in English and Bosnian is required.
Desirable:
- Knowledge of FIDIC contracts is an asset.
- Experience in humanitarian settings is an advantage.
Competencies: planning and organising, working with people, accountability, analysing."""

def cmd_test(_=None):
    a = analyse(SAMPLE)
    assert a["score"] >= 60, a["score"]
    st = {r["text"][:20]: r["status"] for r in a["requirements"]}
    assert any(v == "met" for v in st.values()), st
    assert any(r["desirable"] for r in a["requirements"]), "desirable detection"
    cv = build_cv(a); assert "PROFESSIONAL EXPERIENCE" in cv and "[EMAIL]" in cv
    assert "Bosnian/Croatian/Serbian: Native" in cv
    assert "September 2024 – September 2026" in cv
    cover = build_cover(a, {"title": "Construction Engineer", "org": "UNICEF"}); assert "{" not in cover, cover
    build_cover(a, {"title": "X", "org": "Y"}, "bs")
    assert "Interview" in build_interview(a, SAMPLE, {"title": "X", "org": "Y"})
    assert "NO-C" in build_salary(SAMPLE, {})
    f = parse_issue_form("### Job title\n\nEngineer\n\n### Posting URL\n\n_No response_\n\n### Job posting text\n\nline1\nline2")
    assert f["job title"] == "Engineer" and f["posting url"] == "" and "line2" in f["job posting text"]
    tmp = ROOT / ".selftest.docx"; md_to_docx(cv, tmp)
    with zipfile.ZipFile(tmp) as z: ET.fromstring(z.read("word/document.xml"))
    tmp.unlink()
    phone = "+387 " + "61 000 000"; mail = "x@" + "gmail.com"
    assert find_private(phone) and find_private(mail) and not find_private("Sarajevo")
    assert not find_private(json.dumps(PROFILE, ensure_ascii=False)), "profile.json contains private data"
    wf = ROOT / ".github" / "workflows" / "job-search.yml"; cp = ROOT / "WORKFLOW-job-search.yml"
    if wf.exists() and cp.exists(): assert wf.read_text(encoding="utf-8") == cp.read_text(encoding="utf-8"), "workflow copy differs"
    print(f"all tests passed (sample fit score {a['score']})")

# ======================================================================= CLI
def cmd_apply(a):
    posting = pathlib.Path(a.file).read_text(encoding="utf-8")
    meta = {"title": a.title, "org": a.org, "url": a.url, "lang": a.lang, "issue": "local"}
    an, s = run_apply(posting, meta, APPS / f"local-{slug(a.org + ' ' + a.title)}")
    run_interview(APPS / f"local-{slug(a.org + ' ' + a.title)}")
    print(json.dumps(s, indent=2, ensure_ascii=False))

def cmd_ci(_=None):
    ev_name = os.environ.get("EVENT_NAME", "")
    if ev_name == "schedule": return cmd_daily()
    if ev_name == "workflow_dispatch":
        return {"daily": cmd_daily, "setup": cmd_setup, "dashboard": lambda: build_dashboard(), "test": cmd_test}[os.environ.get("TASK") or "daily"]()
    if ev_name == "issues": return handle_issue(json.loads(pathlib.Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")))
    print(f"event '{ev_name}': checks only")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("ci", "daily", "setup", "dashboard", "check", "test"): sp.add_parser(n)
    s = sp.add_parser("apply"); s.add_argument("--file", required=True); s.add_argument("--title", default="Test role")
    s.add_argument("--org", default="Test org"); s.add_argument("--url", default=""); s.add_argument("--lang", default="en")
    a = ap.parse_args()
    {"ci": cmd_ci, "daily": cmd_daily, "setup": cmd_setup, "dashboard": lambda _: build_dashboard(), "check": cmd_check,
     "test": cmd_test, "apply": cmd_apply}[a.cmd](a)

if __name__ == "__main__":
    main()
