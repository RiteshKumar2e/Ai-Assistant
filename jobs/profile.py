"""
jobs/profile.py — the current user's candidate profile, resumes and job preferences.

    {
      "resumes": [ {id, file, name, uploaded, profile: {...parsed...}, confirmed: bool} ],
      "active": "<resume id>",
      "preferences": {...}, "preferences_set": bool,
      "answers": {"notice_period": "...", ...}     # answers the user chose to reuse on forms
    }

A profile parsed from a resume is not relied on until the user says it is right
(confirm()). Preferences are only what the user said; where they said nothing,
defaults are derived from THEIR resume and listed as defaults, so JUDO can say
"I'll search … — change these if you like".
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date

from jobs import resume as rz
from jobs.store import UserStore

EMPTY = {"resumes": [], "active": "", "preferences": {}, "preferences_set": False, "answers": {}}
PREF_KEYS = ("roles", "locations", "remote", "experience_range", "salary_min", "include_internships", "job_types",
             "international_remote", "fresh_days")


class ProfileError(Exception):
    pass


class Profiles:
    def __init__(self, store: UserStore):
        self.store = store

    # ── storage ────────────────────────────────────────────────────────────
    def _data(self) -> dict:
        d = self.store.load("profile", None) or {}
        return {**{k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in EMPTY.items()}, **d}

    def _save(self, d: dict) -> None:
        self.store.save("profile", d)

    # ── resumes ────────────────────────────────────────────────────────────
    def add_resume(self, path: str, llm=None, today: date | None = None) -> dict:
        """Read the user's resume, keep a private copy, parse it. Returns the resume record
        (unconfirmed). Raises ResumeError for unsupported / unreadable files."""
        text = rz.read_text(path)
        copy = self.store.add_resume(path)
        parsed = rz.parse(text, today=today, llm=llm)
        rid = hashlib.sha1(copy.read_bytes()).hexdigest()[:12]
        d = self._data()
        d["resumes"] = [r for r in d["resumes"] if r["id"] != rid]
        rec = {"id": rid, "file": str(copy), "name": copy.name, "uploaded": time.strftime("%Y-%m-%d %H:%M"),
               "profile": parsed, "confirmed": False, "text_chars": len(text)}
        d["resumes"].append(rec)
        d["active"] = rid
        self._save(d)
        return rec

    def create_manual(self, fields: dict) -> dict:
        """A profile the user typed in themselves (no resume). Only given fields are set."""
        p = {"candidate": {k: str(fields.get(k, "")).strip() for k in ("name", "email", "phone", "location")},
             "summary": "", "education": [], "experience": [], "experience_months": 0, "internship_months": 0,
             "skills": [], "skills_other": [], "skills_by_category": {}, "projects": [], "certifications": [],
             "achievements": [], "links": [], "potential_roles": []}
        rid = "manual"
        d = self._data()
        d["resumes"] = [r for r in d["resumes"] if r["id"] != rid]
        d["resumes"].append({"id": rid, "file": "", "name": "Profile entered manually", "uploaded": time.strftime("%Y-%m-%d %H:%M"),
                             "profile": p, "confirmed": False, "manual": True})
        d["active"] = rid
        self._save(d)
        self.edit(fields)
        return self.active()

    def resumes(self) -> list[dict]:
        return self._data()["resumes"]

    def select(self, which: str) -> dict:
        """Use another of the user's resumes: by id, number (1-based) or words of its file name."""
        d = self._data()
        rs = d["resumes"]
        pick = None
        if str(which).isdigit() and 1 <= int(which) <= len(rs):
            pick = rs[int(which) - 1]
        else:
            w = str(which).lower()
            pick = next((r for r in rs if r["id"] == which or w in r["name"].lower()), None)
        if not pick:
            raise ProfileError("I couldn't find that resume. " + self.describe_resumes())
        d["active"] = pick["id"]
        self._save(d)
        return pick

    def describe_resumes(self) -> str:
        rs = self.resumes()
        if not rs:
            return "No resume uploaded yet."
        active = self._data()["active"]
        return "Your resumes: " + "; ".join(f"[{i}] {r['name']}{' (in use)' if r['id'] == active else ''}"
                                            for i, r in enumerate(rs, 1))

    def active(self) -> dict | None:
        d = self._data()
        return next((r for r in d["resumes"] if r["id"] == d["active"]), None)

    def profile(self) -> dict | None:
        a = self.active()
        return a["profile"] if a else None

    def ready(self) -> bool:
        a = self.active()
        return bool(a and a.get("confirmed"))

    def confirm(self) -> None:
        d = self._data()
        for r in d["resumes"]:
            if r["id"] == d["active"]:
                r["confirmed"] = True
        self._save(d)

    def edit(self, changes: dict) -> dict:
        """Apply the user's own corrections to the active profile. Keys: name, email, phone,
        location, skills (list or comma text — replaces), add_skills, remove_skills,
        experience_years (overrides the computed figure), roles (potential roles)."""
        d = self._data()
        rec = next((r for r in d["resumes"] if r["id"] == d["active"]), None)
        if not rec:
            raise ProfileError("There is no profile yet — upload a resume first.")
        p = rec["profile"]
        lst = lambda v: [s.strip() for s in (v if isinstance(v, list) else str(v).split(",")) if str(s).strip()]
        for k in ("name", "email", "phone", "location"):
            if changes.get(k) not in (None, ""):
                p["candidate"][k] = str(changes[k]).strip()
        if changes.get("skills") not in (None, ""):
            p["skills"] = lst(changes["skills"])
        if changes.get("add_skills"):
            p["skills"] = list(dict.fromkeys(p["skills"] + lst(changes["add_skills"])))
        if changes.get("remove_skills"):
            gone = {s.lower() for s in lst(changes["remove_skills"])}
            p["skills"] = [s for s in p["skills"] if s.lower() not in gone]
        if changes.get("experience_years") not in (None, ""):
            try:
                p["experience_months"] = round(float(changes["experience_years"]) * 12)
                p["experience_user_set"] = True
            except ValueError:
                raise ProfileError("Experience should be a number of years, like 1.5.")
        if changes.get("roles"):
            p["potential_roles"] = lst(changes["roles"])
        if changes.get("work_authorization") not in (None, ""):
            p.setdefault("candidate", {})["work_authorization"] = str(changes["work_authorization"]).strip()
        rec["confirmed"] = False          # an edited profile is shown again before it's relied on
        self._save(d)
        return rec

    # ── answers the user chose to reuse on application forms ────────────────
    def answers(self) -> dict:
        return self._data()["answers"]

    def save_answer(self, key: str, value: str) -> None:
        d = self._data()
        d["answers"][key] = value
        self._save(d)

    # ── preferences ────────────────────────────────────────────────────────
    def preferences(self) -> dict:
        return self._data()["preferences"]

    def preferences_set(self) -> bool:
        return bool(self._data()["preferences_set"])

    def set_preferences(self, prefs: dict) -> dict:
        d = self._data()
        cur = dict(d["preferences"])
        for k in PREF_KEYS:
            if prefs.get(k) not in (None, "", []):
                cur[k] = prefs[k]
        d["preferences"], d["preferences_set"] = cur, True
        self._save(d)
        return cur

    def effective_preferences(self) -> tuple[dict, list[str]]:
        """What a search will use: the user's own choices, with gaps filled from their resume.
        Returns (preferences, names of the fields that are defaults)."""
        p = self.profile() or {}
        own = self.preferences()
        defaults = default_preferences(p)
        merged = {**defaults, **{k: v for k, v in own.items() if v not in (None, "", [])}}
        return merged, [k for k in defaults if own.get(k) in (None, "", [])]


# ── helpers used by the agent and UI ───────────────────────────────────────

def years(profile: dict) -> float:
    return round((profile or {}).get("experience_months", 0) / 12, 1)


def country_of(location: str) -> str:
    parts = [x.strip() for x in (location or "").split(",") if x.strip()]
    return parts[-1] if len(parts) >= 2 else ""


def default_preferences(profile: dict) -> dict:
    y = years(profile)
    lo = max(0, int(y) - 1)
    hi = max(2, int(y) + 2)
    loc = (profile.get("candidate") or {}).get("location", "")
    country = country_of(loc)
    edu_years = [e.get("graduation_year") or 0 for e in profile.get("education", [])]
    student = bool(edu_years) and max(edu_years) > date.today().year and y == 0     # still studying
    return {
        "roles": (profile.get("potential_roles") or [])[:3],
        "locations": [x for x in ([loc.split(",")[0].strip()] if loc else []) + ([country] if country else []) if x],
        "remote": "any",
        "experience_range": [lo, hi],
        "salary_min": None,
        "include_internships": student,
        "job_types": ["internship", "full-time"] if student else ["full-time"],
        "international_remote": False,
        "fresh_days": 7,
    }


def describe_preferences(prefs: dict, defaulted: list[str]) -> str:
    lo, hi = (prefs.get("experience_range") or [0, 2])[:2]
    roles = " / ".join(prefs.get("roles") or []) or "roles matching your resume"
    where = ", ".join(prefs.get("locations") or []) or "any location"
    remote = {"remote": "remote", "hybrid": "hybrid", "onsite": "on-site"}.get(str(prefs.get("remote")), "")
    kinds = " and ".join(prefs.get("job_types") or ["full-time"])
    line = f"{kinds} {roles} roles{(' (' + remote + ')') if remote else ''} in {where} for {lo}–{hi} years of experience"
    if prefs.get("salary_min"):
        line += f", minimum salary {prefs['salary_min']}"
    if prefs.get("international_remote"):
        line += ", plus international remote roles"
    shown = {"roles": "roles", "locations": "locations", "experience_range": "experience level", "job_types": "job type"}
    picked = [shown[k] for k in defaulted if k in shown]
    note = f" — {', '.join(picked)} based on your resume; tell me if you want something different" if picked else ""
    return line + note


def summary(rec: dict) -> str:
    """The concise 'is this right?' summary shown after parsing."""
    p = rec["profile"]
    c = p.get("candidate", {})
    edu = "; ".join(" ".join(x for x in (e.get("degree"), ("in " + e["field"]) if e.get("field") else "",
                                          ("— " + e["institution"]) if e.get("institution") else "",
                                          f"({e['graduation_year']})" if e.get("graduation_year") else "",
                                          f"GPA {e['gpa']}" if e.get("gpa") else "") if x)
                    for e in p.get("education", [])[:3]) or "not found"
    jobs = [f"{e.get('title') or 'Role'}{' at ' + e['company'] if e.get('company') else ''} ({e['start']} – {e['end']})"
            + (" [internship]" if e.get("internship") else "") for e in p.get("experience", [])[:4]]
    y = years(p)
    full = f"{y} years full-time" if y else "no full-time experience yet"
    interns = f"{p['internship_months']} months of internships" if p.get("internship_months") else ""
    exp = ", ".join(x for x in (full, interns) if x) if p.get("experience") or y else "none listed"
    lines = ["Resume processed successfully." if not rec.get("manual") else "Profile created.",
             f"Name: {c.get('name') or 'not found'}",
             f"Education: {edu}",
             f"Experience: {exp}" + ("\n  " + "\n  ".join(jobs) if jobs else ""),
             f"Primary skills: {', '.join(p.get('skills', [])[:12]) or 'not found'}",
             f"Potential roles: {', '.join(p.get('potential_roles', [])[:5]) or 'not clear yet'}",
             f"Location: {c.get('location') or 'not found'}",
             "",
             "Is this information correct? [Looks Correct] [Edit Profile] [Upload Different Resume]"]
    return "\n".join(lines)


def missing_basics(profile: dict) -> list[str]:
    c = (profile or {}).get("candidate", {})
    return [k for k in ("name", "email", "phone") if not c.get(k)]
