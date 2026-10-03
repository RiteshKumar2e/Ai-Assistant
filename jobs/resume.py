"""
jobs/resume.py — read a resume (PDF / DOCX / TXT) and turn it into a candidate profile.

Only what the resume actually says is kept. The rules here find sections,
contact details, links, dates, degrees and skills; an optional model pass may
fill gaps, but every value it returns is checked against the resume text and
dropped if the resume doesn't contain it — a profile is never padded with
plausible guesses. Anything not found stays empty and the user is asked.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from jobs import skills as sk

SUPPORTED = (".pdf", ".docx", ".txt")
UNSUPPORTED_MSG = ("I can read resumes in PDF, DOCX or TXT format. Please upload your resume in one of those "
                   "formats (in Word: File → Save As → PDF or .docx).")

SECTIONS = {
    "summary": ("summary", "professional summary", "profile", "profile summary", "objective", "career objective",
                "about me", "about"),
    "education": ("education", "academic background", "academics", "academic qualifications", "educational qualifications",
                  "education and training"),
    "experience": ("experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "internships", "internship", "internship experience", "experience & internships",
                   "relevant experience", "industry experience"),
    "projects": ("projects", "personal projects", "academic projects", "key projects", "selected projects", "project work",
                 "projects & research"),
    "skills": ("skills", "technical skills", "core skills", "key skills", "skills & tools", "skills and tools",
               "technologies", "tech stack", "technical proficiency", "core competencies", "tools & technologies"),
    "certifications": ("certifications", "certificates", "certification", "licenses & certifications",
                       "licenses and certifications", "courses", "courses & certifications", "online courses"),
    "achievements": ("achievements", "awards", "honors", "honours", "awards & achievements", "accomplishments",
                     "achievements & awards", "extracurricular", "extra-curricular activities", "positions of responsibility"),
    "links": ("links", "contact", "contact information", "personal details", "personal information"),
}
_HEADING = {alias: key for key, aliases in SECTIONS.items() for alias in aliases}

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_POINT = rf"(?:{_MON}\.?,?\s*'?\d{{2,4}}|\d{{1,2}}\s*/\s*\d{{4}}|\d{{4}})"
_NOW = r"(?:present|current|now|till\s+date|to\s+date|ongoing|today)"
RANGE = re.compile(rf"(?P<a>{_POINT})\s*(?:-|–|—|to|until|till)\s*(?P<b>{_POINT}|{_NOW})", re.I)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w/])(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,5}\)?[\s.-]?)?\d{3,5}[\s.-]?\d{3,5}(?![\w/])")
URL = re.compile(r"(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s,;|()<>\"']*)?", re.I)

TITLE_WORDS = ("engineer", "developer", "intern", "analyst", "manager", "scientist", "designer", "consultant", "associate",
               "lead", "architect", "specialist", "administrator", "tester", "trainee", "programmer", "researcher",
               "assistant", "officer", "executive", "head", "director", "fellow", "apprentice", "sde", "member of technical staff")
DEGREES = [  # (pattern, label, level)
    (r"\bph\.?\s?d\b|doctor of philosophy|doctorate", "PhD", 4),
    (r"\bm\.?\s?tech\b|\bm\.?e\.?\b(?=\s|,|$)|master of technology|master of engineering", "M.Tech / M.E.", 3),
    (r"\bm\.?\s?s\.?\b(?=\s+in\b)|\bm\.?sc\b|master of science", "M.S. / M.Sc.", 3),
    (r"\bmba\b|master of business administration", "MBA", 3),
    (r"\bmca\b|master of computer applications", "MCA", 3),
    (r"\bmaster'?s?\b|\bm\.?a\.?\b(?=\s+in\b)", "Master's", 3),
    (r"\bb\.?\s?tech\b|\bb\.?\s?e\.?\b(?=\s|,|$)|bachelor of technology|bachelor of engineering", "B.Tech / B.E.", 2),
    (r"\bb\.?sc\b|\bb\.?s\.?\b(?=\s+in\b)|bachelor of science", "B.Sc. / B.S.", 2),
    (r"\bbca\b|bachelor of computer applications", "BCA", 2),
    (r"\bb\.?com\b|bachelor of commerce", "B.Com", 2),
    (r"\bbba\b|bachelor of business administration", "BBA", 2),
    (r"\bb\.?a\.?\b(?=\s+in\b)|bachelor of arts", "B.A.", 2),
    (r"\bbachelor'?s?\b|\bundergraduate\b", "Bachelor's", 2),
    (r"\bdiploma\b|\bpolytechnic\b", "Diploma", 1),
    (r"\b(?:class|grade)\s*(?:xii|12(?:th)?)\b|\bhsc\b|higher secondary|senior secondary|\bcbse\b|\bisc\b", "Class 12", 0),
]
INSTITUTION = re.compile(r"\b(?:university|institute|college|school|academy|polytechnic|iit|nit|iiit|bits)\b", re.I)
GPA = re.compile(r"\b(?:c?gpa|cpi|sgpa)\b\s*[:\-–]?\s*(\d{1,2}(?:\.\d{1,2})?)\s*(?:/\s*(\d{1,2}(?:\.\d{1,2})?))?"
                 r"|(\d(?:\.\d{1,2})?)\s*/\s*(10|4(?:\.0)?)\b\s*(?:c?gpa|cpi)?", re.I)
BULLET = re.compile(r"^\s*(?:[•●▪◦‣∙·*–—-]|\d+[.)])\s*")


class ResumeError(Exception):
    pass


# ── reading the file ───────────────────────────────────────────────────────

def read_text(path: str | Path) -> str:
    p = Path(path)
    if p.suffix.lower() not in SUPPORTED:
        raise ResumeError(UNSUPPORTED_MSG)
    if not p.is_file():
        raise ResumeError(f"I can't find the file {p.name}.")
    if p.suffix.lower() == ".txt":
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        from agent.tools.documents import extract_text     # the documents reader JUDO already uses
        try:
            text = extract_text(p)
        except Exception as e:
            raise ResumeError(f"I couldn't read {p.name}: {e}") from e
    text = re.sub(r"\[page \d+\]\n?", "", text).replace("\r", "")
    if len(text.strip()) < 40:
        raise ResumeError(f"{p.name} has almost no readable text — if it is a scanned image, please upload a "
                          "text-based PDF or a DOCX.")
    return text


# ── sections ───────────────────────────────────────────────────────────────

def _heading(line: str) -> str | None:
    s = re.sub(r"[:|_•#*=\-–—]+$", "", line.strip()).strip(" :|•#*=").lower()
    s = re.sub(r"\s+", " ", s)
    return _HEADING.get(s) if 0 < len(s) <= 40 else None


def split_sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.split("\n"):
        key = _heading(line)
        if key:
            current = key
            out.setdefault(current, [])
            continue
        out.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _lines(block: str) -> list[str]:
    return [BULLET.sub("", ln).strip() for ln in (block or "").split("\n") if ln.strip()]


# ── dates ──────────────────────────────────────────────────────────────────

def _point(s: str, end: bool, today: date) -> tuple[int, int] | None:
    s = s.strip().lower().replace(".", "").replace(",", "")
    if re.fullmatch(_NOW, s):
        return today.year, today.month
    m = re.match(rf"({_MON})\s*'?(\d{{2,4}})", s)
    if m:
        y = int(m.group(2))
        y = y + 2000 if y < 100 else y
        return y, MONTHS[m.group(1)[:3]]
    m = re.match(r"(\d{1,2})\s*/\s*(\d{4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    m = re.match(r"(\d{4})", s)
    if m and 1950 <= int(m.group(1)) <= today.year + 8:
        return int(m.group(1)), 12 if end else 1
    return None


def date_range(text: str, today: date | None = None) -> dict | None:
    today = today or date.today()
    m = RANGE.search(text or "")
    if not m:
        return None
    a, b = _point(m.group("a"), False, today), _point(m.group("b"), True, today)
    if not a or not b or b < a:
        return None
    months = (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1
    current = bool(re.fullmatch(_NOW, m.group("b").strip().lower()))
    return {"text": m.group(0).strip(), "start": f"{a[0]}-{a[1]:02d}", "end": "present" if current else f"{b[0]}-{b[1]:02d}",
            "months": max(1, months), "current": current, "_span": (a, b)}


def total_months(spans: list[tuple[tuple[int, int], tuple[int, int]]]) -> int:
    """Months covered by these periods, overlapping jobs counted once."""
    covered: set[tuple[int, int]] = set()
    for (ay, am), (by, bm) in spans:
        y, m = ay, am
        while (y, m) <= (by, bm):
            covered.add((y, m))
            m += 1
            if m > 12:
                y, m = y + 1, 1
    return len(covered)


# ── pieces ─────────────────────────────────────────────────────────────────

def _contact(text: str, header: str) -> dict:
    emails = EMAIL.findall(text)
    email = emails[0] if emails else ""
    phone = ""
    for m in PHONE.finditer(header or text[:1500]):
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 15 and not re.fullmatch(r"(19|20)\d{2}(19|20)\d{2}", digits):
            phone = m.group(0).strip()
            break
    return {"email": email, "phone": phone}


def _links(text: str) -> list[dict]:
    out, seen = [], set()
    for m in URL.finditer(text):
        raw = m.group(0).rstrip(".,;:)")
        if "@" in text[max(0, m.start() - 1):m.start()] or EMAIL.fullmatch(raw):
            continue
        low = raw.lower()
        if not re.search(r"/|linkedin|github|gitlab|behance|dribbble|medium|kaggle|leetcode|portfolio|\.dev\b|\.io\b|\.me\b|vercel|netlify", low):
            continue                                         # plain words like "node.js" are not links
        if sk.find_skills(raw) and "/" not in raw:
            continue
        kind = ("linkedin" if "linkedin.com" in low else "github" if "github.com" in low else "gitlab" if "gitlab.com" in low
                else "leetcode" if "leetcode" in low else "kaggle" if "kaggle" in low else "portfolio")
        url = raw if low.startswith("http") else "https://" + raw
        if url.lower() not in seen:
            seen.add(url.lower())
            out.append({"type": kind, "url": url})
    return out


def _name(header: str, email: str) -> str:
    for line in _lines(header)[:6]:
        clean = re.sub(r"\s+", " ", line).strip()
        if (EMAIL.search(clean) or re.search(r"\d|https?://|www\.|\||@", clean) or _heading(clean)
                or not 1 < len(clean.split()) <= 5):
            continue
        if re.fullmatch(r"[A-Za-zÀ-ÿ'.\- ]+", clean) and not any(w in clean.lower() for w in TITLE_WORDS + ("resume", "curriculum", "vitae")):
            return clean.title() if clean.isupper() else clean
    return ""


def _location(header: str) -> str:
    """A 'City, State/Country' piece of the contact header — or nothing."""
    for part in re.split(r"\s*[|•·◦]\s*|\n|\s{3,}", header or ""):
        part = part.strip(" ,")
        part = re.sub(r"(?i)^(?:location|address|based in)\s*[:\-]\s*", "", part)
        if (EMAIL.search(part) or URL.search(part) and "." in part or re.search(r"\d{3,}", part) or len(part) > 60):
            continue
        if re.fullmatch(r"[A-Z][A-Za-z.\- ]+,\s*[A-Z][A-Za-z.\- ]+(?:,\s*[A-Z][A-Za-z.\- ]+)?", part):
            return part
    return ""


def _split_role(line: str) -> tuple[str, str]:
    """'Software Engineer — Acme Corp' / 'Acme Corp | Backend Intern' → (title, company)."""
    parts = [p.strip(" ,") for p in re.split(r"\s+(?:—|–|-|\||@|at)\s+|\s*\|\s*", line) if p.strip(" ,")]
    if len(parts) == 1:                                   # "Software Engineer, Acme Corp"
        parts = [p.strip() for p in re.split(r",\s+(?=[A-Z])", line) if p.strip()]
    if not parts:
        return "", ""
    titled = [p for p in parts if any(w in p.lower() for w in TITLE_WORDS)]
    title = titled[0] if titled else ""
    rest = [p for p in parts if p != title and not RANGE.search(p)]
    company = rest[0] if rest else ""
    if not title and len(parts) == 1:
        company = parts[0]
    return title, company


def _experience(block: str, section_is_internships: bool, today: date) -> list[dict]:
    """Each job starts at a line with a date range. Up to two plain (non-bullet) lines just
    above it can hold the title / company; bullets and prose below it are its details."""
    rows = [(BULLET.sub("", ln).strip(), bool(BULLET.match(ln))) for ln in (block or "").split("\n") if ln.strip()]
    starts = [i for i, (ln, _) in enumerate(rows) if date_range(ln, today)]
    heads: list[list[int]] = []          # for each job, the row indexes that make up its heading
    for n, i in enumerate(starts):
        floor = starts[n - 1] + 1 if n else 0
        above = []
        for j in range(i - 1, max(floor, i - 2) - 1, -1):
            text, bullet = rows[j]
            if bullet or len(text) > 90 or text.endswith("."):
                break
            above.insert(0, j)
        heads.append(above + [i])
    entries: list[dict] = []
    for n, i in enumerate(starts):
        line = rows[i][0]
        rng = date_range(line, today)
        head = RANGE.sub("", line).strip(" ,|–—-()")
        title, company = _split_role(head) if head else ("", "")
        for j in reversed(heads[n][:-1]):
            t2, c2 = _split_role(rows[j][0])
            if not title and t2:
                title = t2
            if not company and c2 and c2 != title:
                company = c2
        stop = heads[n + 1][0] if n + 1 < len(starts) else len(rows)
        details = [rows[j][0] for j in range(i + 1, stop)][:12]
        heading = " ".join(rows[j][0] for j in heads[n])
        entries.append({"title": title, "company": company, "start": rng["start"], "end": rng["end"],
                        "months": rng["months"], "current": rng["current"], "_span": rng["_span"],
                        "internship": section_is_internships or bool(re.search(r"(?i)\bintern|trainee|apprentice", heading)),
                        "details": details, "skills": sk.find_skills(" ".join([title] + details))})
    return entries


def _education(block: str, today: date) -> list[dict]:
    lines = _lines(block)
    groups: list[list[str]] = []
    for line in lines:
        starts_new = any(re.search(p, line, re.I) for p, _, _ in DEGREES) or INSTITUTION.search(line)
        if starts_new and groups and not (len(groups[-1]) == 1 and (INSTITUTION.search(groups[-1][0])
                                                                    != INSTITUTION.search(line))):
            groups.append([line])
        elif not groups:
            groups.append([line])
        else:
            groups[-1].append(line)
    out = []
    for g in groups:
        text = " ".join(g)
        degree, level = "", -1
        for pat, label, lv in DEGREES:
            if re.search(pat, text, re.I):
                degree, level = label, lv
                break
        field = ""
        stop = r"(?=\s*(?:[,|()–—-]|\d|$|from\b|at\b))"
        degree_line = next((ln for ln in g if any(re.search(p, ln, re.I) for p, _, _ in DEGREES)), text)
        degree_line = INSTITUTION.split(degree_line)[0] if INSTITUTION.search(degree_line) else degree_line
        m = re.search(rf"\bin\s+([A-Z][A-Za-z&/ ]{{3,60}}?){stop}", degree_line)
        if degree and m and not INSTITUTION.search(m.group(1)):
            field = m.group(1).strip()
        inst = next((ln for ln in g if INSTITUTION.search(ln)), "")
        inst = RANGE.sub("", inst)
        inst = re.split(r"\s+(?:—|–|-|\|)\s+|\s*\|\s*", inst)
        inst = next((p.strip(" ,") for p in inst if INSTITUTION.search(p)), "")
        years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20\d{2})\b", text) if int(y) <= today.year + 6]
        gpa = ""
        gm = GPA.search(text)
        if gm:
            value, scale = (gm.group(1), gm.group(2)) if gm.group(1) else (gm.group(3), gm.group(4))
            gpa = f"{value}/{scale}" if scale else value
        pct = re.search(r"\b(\d{2}(?:\.\d{1,2})?)\s*%", text)
        if not (degree or inst):
            continue
        out.append({"degree": degree, "level": level, "field": field, "institution": inst,
                    "graduation_year": max(years) if years else None, "gpa": gpa,
                    "percentage": pct.group(1) + "%" if pct and not gpa else "", "raw": text[:300]})
    return out


def _projects(block: str) -> list[dict]:
    out: list[dict] = []
    for raw in (block or "").split("\n"):
        if not raw.strip():
            continue
        bulleted = bool(BULLET.match(raw)) and not re.match(r"^\s*\d+[.)]", raw)
        line = BULLET.sub("", raw).strip()
        is_title = (not bulleted and len(line) <= 120 and not line.endswith(".")) or not out
        if is_title:
            name, *rest = re.split(r"\s+(?:—|–|-|\|)\s+|\s*\|\s*|:\s+", line)
            name = name.strip()
            out.append({"name": name[:90], "details": [" ".join(rest).strip()] if " ".join(rest).strip() else []})
        else:
            out[-1]["details"].append(line)
    for p in out:
        p["skills"] = sk.find_skills(" ".join([p["name"]] + p["details"]))
        p["details"] = p["details"][:8]
    return [p for p in out if p["name"]]


def _listed_skills(block: str) -> list[str]:
    items: list[str] = []
    for line in _lines(block):
        line = re.sub(r"^[A-Za-z &/]{2,30}:\s*", "", line)                # "Languages: Python, Java"
        for piece in re.split(r"\s*[,;|•·/]\s*|\s{2,}", line):
            piece = piece.strip(" .")
            if 1 < len(piece) <= 40 and not re.search(r"\d{4}", piece):
                items.append(piece)
    return items


def _roles(experience: list[dict], skills: list[str]) -> list[str]:
    """Roles this resume points to: the titles actually held, then what the skills suggest."""
    roles: list[str] = []
    for e in sorted(experience, key=lambda e: (e["internship"], e["end"] != "present")):
        t = re.sub(r"(?i)\b(?:summer\s+)?(?:intern(?:ship)?|trainee|apprentice)\b", "", e["title"]).strip(" -–—,")
        if t and any(w in t.lower() for w in TITLE_WORDS) and t.lower() not in (r.lower() for r in roles) and len(t) < 50:
            roles.append(t)
    s = set(skills)
    backend = s & {"Django", "Flask", "FastAPI", "Spring", "Node.js", "Express", ".NET", "Laravel", "Rails", "REST APIs",
                   "Microservices", "GraphQL"}
    frontend = s & {"React", "Angular", "Vue", "Next.js", "Redux", "Tailwind CSS"}
    ml = s & {"Machine Learning", "Deep Learning", "TensorFlow", "PyTorch", "NLP", "Computer Vision", "Generative AI",
              "scikit-learn", "Hugging Face", "LangChain"}
    data = s & {"Data Analysis", "Pandas", "Tableau", "Power BI", "Excel", "BigQuery", "Snowflake"}
    mobile = s & {"Android", "iOS", "Flutter", "React Native", "Kotlin", "Swift", "Dart"}
    ops = s & {"Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins", "CI/CD", "DevOps", "AWS", "Azure", "GCP"}
    suggested = []
    if backend and frontend:
        suggested.append("Full Stack Developer")
    if backend:
        suggested.append("Backend Developer")
    if frontend:
        suggested.append("Frontend Developer")
    if len(ml) >= 3:
        suggested += ["Machine Learning Engineer", "AI Engineer"]
    if len(data) >= 2:
        suggested.append("Data Analyst")
    if len(mobile) >= 2:
        suggested.append("Mobile App Developer")
    if len(ops) >= 3:
        suggested.append("DevOps Engineer")
    if any(sk.category(x) == "language" for x in s) and (backend or frontend or not suggested):
        suggested.append("Software Engineer")
    for r in suggested:
        if r.lower() not in (x.lower() for x in roles):
            roles.append(r)
    return roles[:8]


# ── the profile ────────────────────────────────────────────────────────────

def parse(text: str, today: date | None = None, llm=None) -> dict:
    """Resume text → profile dict (schema in jobs/profile.py). Pure: nothing is saved here."""
    today = today or date.today()
    sec = split_sections(text)
    header = sec.get("header", "")
    contact = _contact(text, header)
    experience: list[dict] = []
    for key in ("experience",):
        if sec.get(key):
            experience += _experience(sec[key], False, today)
    # an "Internships" heading maps to experience too; mark entries from it
    if re.search(r"(?im)^\s*internships?\s*:?\s*$", text) and not any(e["internship"] for e in experience):
        for e in experience:
            e["internship"] = e["internship"] or "intern" in e["title"].lower()
    listed = _listed_skills(sec.get("skills", ""))
    found_all = sk.find_skills(text)
    primary = sk.find_skills(" , ".join(listed)) or found_all
    extra = [x for x in listed if not sk.find_skills(x)][:25]
    skills = list(dict.fromkeys(primary + [s for s in found_all if s not in primary]))
    full_spans = [e["_span"] for e in experience if not e["internship"]]
    intern_spans = [e["_span"] for e in experience if e["internship"]]
    for e in experience:
        e.pop("_span", None)
    profile = {
        "candidate": {"name": _name(header, contact["email"]), "email": contact["email"], "phone": contact["phone"],
                      "location": _location(header)},
        "summary": " ".join(_lines(sec.get("summary", "")))[:800],
        "education": _education(sec.get("education", ""), today),
        "experience": experience,
        "experience_months": total_months(full_spans),
        "internship_months": total_months(intern_spans),
        "skills": skills,
        "skills_other": extra,
        "skills_by_category": {},
        "projects": _projects(sec.get("projects", "")),
        "certifications": _lines(sec.get("certifications", ""))[:20],
        "achievements": _lines(sec.get("achievements", ""))[:20],
        "links": _links(text),
    }
    for s in skills:
        profile["skills_by_category"].setdefault(sk.category(s), []).append(s)
    profile["potential_roles"] = _roles(experience, skills)
    if llm is not None:
        _fill_gaps_with_model(profile, text, llm)
    return profile


def _in_text(value: str, text: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9@+#.]+", " ", (s or "").lower()).strip()
    v = norm(value)
    return bool(v) and v in norm(text)


def _fill_gaps_with_model(profile: dict, text: str, llm) -> None:
    """Ask the model only for fields the rules left empty; keep an answer only if the
    resume itself contains it (so a hallucinated company or title can't get in)."""
    from agent.llm import ask_json
    gaps = [k for k in ("name", "location") if not profile["candidate"].get(k)]
    weak = [i for i, e in enumerate(profile["experience"]) if not (e["title"] and e["company"])]
    if not gaps and not weak:
        return
    prompt = ("Extract from this resume ONLY what is written in it. Reply with JSON: "
              '{"name": "", "location": "", "roles": [{"title": "", "company": "", "dates": ""}]}. '
              "Use empty strings for anything not stated. Do not guess.\n\nRESUME:\n" + text[:8000])
    try:
        got = ask_json(llm, prompt)
    except Exception:
        return
    for k in gaps:
        v = str(got.get(k) or "").strip()
        if v and _in_text(v, text):
            profile["candidate"][k] = v
    for role in got.get("roles") or []:
        for i in weak:
            e = profile["experience"][i]
            if role.get("dates") and date_range(str(role["dates"])) and date_range(str(role["dates"]))["start"] == e["start"]:
                for f in ("title", "company"):
                    v = str(role.get(f) or "").strip()
                    if not e[f] and v and _in_text(v, text):
                        e[f] = v
