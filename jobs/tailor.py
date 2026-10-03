"""
jobs/tailor.py — a version of the user's resume arranged for one job.

What it may do: put the skills and projects this job cares about first, lead the
summary with the job's wording where the resume already supports it, and lay the
whole thing out plainly for applicant-tracking systems.

What it may never do: add a skill, technology, employer, title, project, date,
degree or number that is not in the user's own resume. verify() enforces that on
the finished text — anything that fails is not offered to the user at all.
The tailored resume is saved as DOCX + TXT in the user's own folder and shown to
them before it is used for any application.
"""
from __future__ import annotations

import re
from pathlib import Path

from jobs import jd as jdmod
from jobs import skills as sk
from jobs.match import _implied, candidate_skills


class TailorError(Exception):
    pass


def _rank(items: list, words: set[str], key) -> list:
    return sorted(items, key=lambda x: -len(words & set(key(x))))


def tailor(profile: dict, job: dict) -> dict:
    """→ {"text": plain resume, "sections": {...}, "changes": [what was reordered]}"""
    a = jdmod.analyse(job.get("description", ""), job.get("title", ""))
    wanted = set(a["required_skills"]) | set(a["preferred_skills"]) | set(a["mentioned_skills"])
    own = candidate_skills(profile)
    have = _implied(own)
    listed = list(profile.get("skills", []))
    # skills: the job's ones (that the resume has) first, then the rest — nothing added
    front = [s for s in listed if s in wanted]
    skills = front + [s for s in listed if s not in front]
    projects = _rank(profile.get("projects", []), wanted, lambda p: p.get("skills", []))
    experience = list(profile.get("experience", []))          # order and dates stay as they are
    c = profile.get("candidate", {})
    title_words = job.get("title", "")
    focus = [s for s in a["required_skills"] if s in have and s in listed][:5]
    summary = profile.get("summary", "")
    if focus:
        lead = f"Relevant skills for {title_words}: {', '.join(focus)}." if title_words else ""
        summary = (lead + (" " + summary if summary else "")).strip()

    lines: list[str] = []
    add = lines.append
    add(c.get("name", ""))
    contact = " | ".join(x for x in (c.get("location"), c.get("email"), c.get("phone")) if x)
    if contact:
        add(contact)
    links = " | ".join(l["url"].replace("https://", "") for l in profile.get("links", []))
    if links:
        add(links)
    if summary:
        add("")
        add("SUMMARY")
        add(summary)
    add("")
    add("SKILLS")
    by_cat: dict[str, list[str]] = {}
    for s in skills:
        by_cat.setdefault(sk.category(s), []).append(s)
    names = {"language": "Languages", "framework": "Frameworks & Libraries", "database": "Databases",
             "cloud": "Cloud & DevOps", "tool": "Tools", "practice": "Areas", "other": "Other"}
    for cat in ("language", "framework", "database", "cloud", "tool", "practice", "other"):
        if by_cat.get(cat):
            add(f"{names[cat]}: {', '.join(by_cat[cat])}")
    other = [x for x in profile.get("skills_other", []) if x]
    if other:
        add(f"Also: {', '.join(other)}")
    if experience:
        add("")
        add("EXPERIENCE")
        for e in experience:
            head = " — ".join(x for x in (e.get("title"), e.get("company")) if x)
            add(f"{head} | {e['start']} – {e['end']}")
            for d in _rank(e.get("details", []), wanted, sk.find_skills):
                add(f"• {d}")
    if projects:
        add("")
        add("PROJECTS")
        for p in projects:
            add(p["name"])
            for d in p.get("details", []):
                add(f"• {d}")
    if profile.get("education"):
        add("")
        add("EDUCATION")
        for e in profile["education"]:
            add(e.get("raw") or " ".join(x for x in (e.get("degree"), e.get("institution")) if x))
    for key, head in (("certifications", "CERTIFICATIONS"), ("achievements", "ACHIEVEMENTS")):
        if profile.get(key):
            add("")
            add(head)
            for x in profile[key]:
                add(f"• {x}")
    text = "\n".join(lines).strip() + "\n"
    changes = []
    if front:
        changes.append(f"Skills this job asks for are listed first: {', '.join(front[:8])}.")
    if projects and projects != profile.get("projects", []):
        changes.append(f"Projects reordered — most relevant first: {projects[0]['name']}.")
    if focus:
        changes.append("Summary now opens with the job's required skills that your resume already shows.")
    missing = [s for s in a["required_skills"] if s not in have]
    if missing:
        changes.append(f"Not added (not on your resume): {', '.join(missing)}.")
    return {"text": text, "changes": changes, "focus": focus}


def verify(original_text: str, profile: dict, tailored_text: str) -> list[str]:
    """Problems that make a tailored resume unusable — empty list means it's honest."""
    problems = []
    orig_skills = set(sk.find_skills(original_text)) | set(profile.get("skills", []))
    new_skills = set(sk.find_skills(tailored_text)) - orig_skills
    if new_skills:
        problems.append(f"adds skills not on the resume: {', '.join(sorted(new_skills))}")
    num = lambda t: set(re.findall(r"\b\d+(?:\.\d+)?%?\+?", t))
    allowed = num(original_text) | {str(x) for e in profile.get("experience", []) for x in re.findall(r"\d+", e["start"] + e["end"])}
    new_nums = {n for n in num(tailored_text) - allowed if not re.fullmatch(r"(?:19|20)\d{2}|0?[1-9]|1[0-2]", n)}
    if new_nums:
        problems.append(f"adds numbers not on the resume: {', '.join(sorted(new_nums))}")
    norm = lambda s: re.sub(r"\s+", " ", (s or "").lower()).strip()
    orig = norm(original_text)
    for e in profile.get("experience", []):
        for f in ("title", "company"):
            if e.get(f) and norm(e[f]) not in orig:
                problems.append(f"{f} '{e[f]}' isn't in the original resume")
    for line in tailored_text.split("\n"):
        m = re.match(r"(.+?) — (.+?) \| (\d{4}-\d{2}) – (\S+)", line)
        if m and not any(m.group(3) == e["start"] and m.group(4) == e["end"] for e in profile.get("experience", [])):
            problems.append(f"dates changed in '{line[:60]}'")
    proj_block = re.search(r"\nPROJECTS\n(.*?)(?:\n\n[A-Z]|\Z)", tailored_text, re.S)
    if proj_block:
        names = [ln for ln in proj_block.group(1).split("\n") if ln and not ln.startswith("•")]
        known = {norm(p["name"]) for p in profile.get("projects", [])}
        fake = [n for n in names if norm(n) not in known]
        if fake:
            problems.append(f"projects not on the resume: {', '.join(fake)}")
    return problems


def save(store, job: dict, result: dict) -> dict:
    """Write TXT + DOCX into the user's own resumes folder."""
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{job.get('company', 'job')}_{job.get('title', '')}")[:60].strip("_")
    base = store.resumes / f"tailored_{safe}_{job['id']}"
    txt = base.with_suffix(".txt")
    txt.write_text(result["text"], encoding="utf-8")
    out = {"txt": str(txt), "docx": ""}
    try:
        import docx
        d = docx.Document()
        for i, line in enumerate(result["text"].split("\n")):
            if i == 0:
                d.add_heading(line, level=0)
            elif re.fullmatch(r"[A-Z][A-Z &]+", line.strip()):
                d.add_heading(line.title(), level=1)
            elif line.startswith("• "):
                d.add_paragraph(line[2:], style="List Bullet")
            else:
                d.add_paragraph(line)
        d.save(str(base.with_suffix(".docx")))
        out["docx"] = str(base.with_suffix(".docx"))
    except Exception:
        pass
    return out
