"""
jobs/match.py — does THIS user's resume fit THIS job, and why.

Classification is decided by the job's requirements, in order of weight:

    hard requirements   experience required, degree required, location / on-site, posting closed,
                        the user's own preferences (internships, remote-only, salary)
    required skills     share of the "must have" skills the resume shows
    softer signals      preferred skills, seniority words, overqualification

A single unmet hard requirement makes a job NOT ELIGIBLE; a shortfall that a
recruiter might overlook (internships instead of full-time years, one missing
skill, "or equivalent experience", a different city) makes it POTENTIALLY
ELIGIBLE. Keyword counts never override a requirement. Every conclusion carries
the sentence from the job description it came from.

A skill counts when the resume lists it, or lists something that implies it
(PostgreSQL → SQL, Django → Python). Nothing is assumed beyond that.
"""
from __future__ import annotations

import re

from jobs import jd as jdmod
from jobs import skills as sk
from jobs.profile import country_of

ELIGIBLE, POTENTIAL, NOT = "ELIGIBLE", "POTENTIALLY ELIGIBLE", "NOT ELIGIBLE"

IMPLIES = {"PostgreSQL": "SQL", "MySQL": "SQL", "SQL Server": "SQL", "Oracle": "SQL", "SQLite": "SQL", "BigQuery": "SQL",
           "Snowflake": "SQL", "Django": "Python", "Flask": "Python", "FastAPI": "Python", "Pandas": "Python",
           "PyTorch": "Python", "Spring": "Java", "Express": "Node.js", "Next.js": "React", "Redux": "React",
           "React": "JavaScript", "TypeScript": "JavaScript", "Node.js": "JavaScript", "Angular": "TypeScript",
           "Keras": "Deep Learning", "PyTorch*": "Deep Learning", "TensorFlow": "Deep Learning",
           "Deep Learning": "Machine Learning", "scikit-learn": "Machine Learning", "Computer Vision": "Machine Learning",
           "NLP": "Machine Learning", "Kubernetes": "Docker", "GitHub Actions": "CI/CD", "Jenkins": "CI/CD",
           "Pytest": "Unit Testing", "Jest": "Unit Testing", "JUnit": "Unit Testing", "FastAPI*": "REST APIs",
           "Express*": "REST APIs", "Django*": "REST APIs", "Flask*": "REST APIs"}


def _implied(skills: list[str]) -> dict[str, str]:
    """skill → how the resume shows it ('' = listed itself, else the skill that implies it)."""
    have = {s: "" for s in skills}
    changed = True
    while changed:
        changed = False
        for src, dst in IMPLIES.items():
            src = src.rstrip("*")
            if src in have and dst not in have:
                have[dst] = src
                changed = True
    return have


def candidate_skills(profile: dict) -> list[str]:
    own = list(profile.get("skills", []))
    for extra in profile.get("skills_other", []):
        own += sk.find_skills(extra)
    for part in profile.get("experience", []) + profile.get("projects", []):
        own += part.get("skills", [])
    return list(dict.fromkeys(own))


def _years(profile: dict) -> tuple[float, float]:
    return profile.get("experience_months", 0) / 12, profile.get("internship_months", 0) / 12


def _level(profile: dict) -> int:
    return max([e.get("level", -1) for e in profile.get("education", [])] or [-1])


def match(job: dict, profile: dict, prefs: dict | None = None) -> dict:
    prefs = prefs or {}
    desc = job.get("description") or ""
    a = jdmod.analyse(desc, job.get("title", ""))
    have = _implied(candidate_skills(profile))
    blockers, concerns, notes = [], [], []

    # ── the posting itself ────────────────────────────────────────────────
    if job.get("status") == "expired":
        blockers.append("This posting is closed or no longer accepting applications.")
    if len(jdmod.clean(desc)) < 250:
        concerns.append("I couldn't read a full job description, so this match is based on limited information.")

    # ── experience ────────────────────────────────────────────────────────
    full, intern = _years(profile)
    exp = a["experience"]
    exp_out = {"status": "not stated", "text": ""}
    if exp:
        lo, hi = exp["min"], exp["max"]
        rng = f"{lo:g}–{hi:g}" if hi is not None else f"{lo:g}+"
        exp_out["text"] = exp["text"]
        if full >= lo:
            exp_out["status"] = f"{rng} years {'required' if exp['required'] else 'preferred'} — you have {full:.1f} years: OK"
            if hi is not None and full > hi + 2:
                concerns.append(f"You may be overqualified: the role asks for {rng} years and you have {full:.1f}.")
        elif full + intern >= lo and lo <= 2:
            exp_out["status"] = (f"{rng} years {'required' if exp['required'] else 'preferred'} — you have "
                                 f"{full:.1f} years full-time plus {intern:.1f} years of internships")
            concerns.append(f"The role asks for {rng} years; your experience is mostly internships ({intern:.1f} years).")
        elif not exp["required"] and lo - full <= 2:
            exp_out["status"] = f"{rng} years preferred — you have {full:.1f}"
            concerns.append(f"{rng} years is preferred (not required); you have {full:.1f}.")
        else:
            exp_out["status"] = f"{rng} years {'required' if exp['required'] else 'preferred'} — you have {full:.1f}: not enough"
            blockers.append(f"Needs {rng} years of experience (“{exp['text'][:120]}”); you have {full:.1f} years full-time.")
    if exp and prefs.get("experience_range"):
        want_lo, want_hi = (list(prefs["experience_range"]) + [None, None])[:2]
        if want_hi is not None and exp["min"] > float(want_hi):
            blockers.append(f"Asks for {exp['min']:g}+ years — outside your {want_lo:g}–{want_hi:g} years filter.")
    if not exp and a["senior_title"] and full < 3:
        exp_out["status"] = f"senior-level title — you have {full:.1f} years"
        blockers.append(f"“{job.get('title', '')}” is a senior-level role; you have {full:.1f} years full-time.")

    # ── education ─────────────────────────────────────────────────────────
    edu = a["education"]
    edu_out = {"status": "not stated", "text": ""}
    level = _level(profile)
    if edu:
        edu_out["text"] = edu["text"]
        if level >= edu["level"]:
            edu_out["status"] = f"{edu['label']} {'required' if edu['required'] else 'preferred'} — you meet it"
        elif level < 0:
            edu_out["status"] = f"{edu['label']} asked — no degree found on your resume"
            concerns.append(f"The job asks for a {edu['label']} degree and I couldn't find your degree on the resume.")
        elif edu["required"] and not edu["equivalent_ok"]:
            edu_out["status"] = f"{edu['label']} required — not met"
            blockers.append(f"Requires a {edu['label']} degree (“{edu['text'][:120]}”).")
        else:
            edu_out["status"] = f"{edu['label']} {'preferred' if not edu['required'] else 'or equivalent'} — not met"
            concerns.append(f"{edu['label']} is preferred or accepts equivalent experience.")
    grad = max([e.get("graduation_year") or 0 for e in profile.get("education", [])] or [0])
    import datetime as _dt
    if grad and grad > _dt.date.today().year and not a["internship"]:
        concerns.append(f"You graduate in {grad}; check whether the role accepts candidates who haven't graduated yet.")

    # ── skills ────────────────────────────────────────────────────────────
    req, pref = a["required_skills"], a["preferred_skills"]
    matched_req = [(s, have[s]) for s in req if s in have]
    missing_req = [s for s in req if s not in have]
    matched_pref = [(s, have[s]) for s in pref if s in have]
    missing_pref = [s for s in pref if s not in have]
    if req:
        cover = len(matched_req) / len(req)
        if cover < 0.5 and len(missing_req) >= 2:
            blockers.append(f"Missing most required skills: {', '.join(missing_req)}.")
        elif missing_req:
            concerns.append(f"Missing required skill{'s' if len(missing_req) > 1 else ''}: {', '.join(missing_req)}.")
    else:
        mentioned = a["mentioned_skills"] + pref
        cover = (sum(1 for s in mentioned if s in have) / len(mentioned)) if mentioned else 0.5
        if not mentioned:
            notes.append("The description doesn't list specific skills.")
        elif cover < 0.3:
            concerns.append("Few of the skills this job mentions appear on your resume.")

    # ── location / work mode ──────────────────────────────────────────────
    loc_out = _location(job, a, profile, prefs, blockers, concerns)

    # ── work authorisation (only from what the user told us) ──────────────
    auth = ""
    if a["work_authorization"]:
        stored = (profile.get("candidate") or {}).get("work_authorization", "")
        auth = f"Job says: “{a['work_authorization'][:140]}”. " + (f"Your profile: {stored}." if stored
                                                                    else "USER INPUT REQUIRED — I don't know your work authorisation.")
        if not stored:
            concerns.append("The job mentions work authorisation/sponsorship — you'll need to answer that yourself.")

    # ── the user's own preferences ────────────────────────────────────────
    kinds = [k.lower() for k in prefs.get("job_types") or []]
    if a["internship"] and kinds and "internship" not in kinds and not prefs.get("include_internships"):
        blockers.append("This is an internship and your preferences are for full-time roles.")
    if job.get("salary_max") and prefs.get("salary_min"):
        try:
            if float(job["salary_max"]) < float(prefs["salary_min"]):
                blockers.append(f"Pays up to {job['salary_max']}, below your minimum of {prefs['salary_min']}.")
        except (TypeError, ValueError):
            pass

    cls = NOT if blockers else POTENTIAL if concerns else ELIGIBLE
    if cls == ELIGIBLE and job.get("verified") is False:
        cls = POTENTIAL
    score = round(100 * (0.55 * cover + 0.15 * (len(matched_pref) / len(pref) if pref else 0.5)
                         + 0.15 * (0 if any("experience" in b.lower() or "years" in b.lower() for b in blockers) else 1)
                         + 0.15 * (0 if loc_out["ok"] is False else 1)))
    return {"classification": cls, "score": score,
            "matched_required": matched_req, "missing_required": missing_req,
            "matched_preferred": matched_pref, "missing_preferred": missing_pref,
            "experience": exp_out, "education": edu_out, "location": loc_out, "authorization": auth,
            "blockers": blockers, "concerns": concerns, "notes": notes}


def _location(job, a, profile, prefs, blockers, concerns) -> dict:
    where = (job.get("location") or "").strip()
    mode = job.get("work_mode") or a["work_mode"] or ""
    if re.search(r"(?i)\bremote\b", where):
        mode = "remote"
    user_loc = (profile.get("candidate") or {}).get("location", "")
    user_country = country_of(user_loc).lower()
    wanted = [w.lower() for w in (prefs.get("locations") or []) if w]
    pref_mode = str(prefs.get("remote") or "any").lower()
    out = {"job": where or "not stated", "mode": mode or "not stated", "ok": None}
    low = where.lower()
    if pref_mode == "remote" and mode in ("onsite", "hybrid"):
        blockers.append(f"This role is {mode}; you asked for remote roles.")
        out["ok"] = False
        return out
    if pref_mode == "onsite" and mode == "remote":
        concerns.append("This is a remote role; you asked for on-site roles.")
    if mode == "remote":
        restricted = re.search(r"(?i)remote\s*[-–(,]\s*([A-Za-z .]+)", where)
        region = (restricted.group(1).strip() if restricted else "").lower()
        if region and user_country and user_country not in region and region not in ("anywhere", "worldwide", "global"):
            if prefs.get("international_remote"):
                concerns.append(f"Remote, but limited to {region.title()} — check whether they hire from {user_country.title()}.")
                out["ok"] = None
            else:
                blockers.append(f"Remote only within {region.title()}; you're in {user_country.title()}.")
                out["ok"] = False
        else:
            out["ok"] = True
        return out
    if not where:
        concerns.append("The job location isn't stated.")
        return out
    if wanted and any(w in low for w in wanted):
        out["ok"] = True
    elif user_country and user_country in low:
        out["ok"] = None
        concerns.append(f"The job is in {where}, a different city from your preferences — relocation may be needed.")
    elif wanted or user_country:
        out["ok"] = False
        blockers.append(f"The job is {mode or 'based'} in {where}, outside your preferred locations.")
    return out


def explain(m: dict) -> str:
    """The reasoning, in the shape the user sees it."""
    via = lambda pairs: ", ".join(f"{s} (via {v})" if v else s for s, v in pairs) or "—"
    lines = [m["classification"] + f"  ·  match {m['score']}/100"]
    lines.append("MATCHED: " + via(m["matched_required"] + m["matched_preferred"]))
    lines.append("MISSING (required): " + (", ".join(m["missing_required"]) or "none"))
    if m["missing_preferred"]:
        lines.append("MISSING (nice to have): " + ", ".join(m["missing_preferred"]))
    lines.append("EXPERIENCE: " + m["experience"]["status"])
    lines.append("EDUCATION: " + m["education"]["status"])
    lines.append(f"LOCATION: {m['location']['job']} ({m['location']['mode']})")
    if m["authorization"]:
        lines.append("WORK AUTHORISATION: " + m["authorization"])
    for b in m["blockers"]:
        lines.append("✗ " + b)
    for c in m["concerns"]:
        lines.append("! " + c)
    return "\n".join(lines)
