"""
jobs/service.py — the Job Agent's flow for one user, used by the voice plugin
(plugins/job_agent.py), the multi-agent supervisor (agent/agents/jobs.py) and the
JUDO Jobs window (jobs/window.py). Every method returns what JUDO should tell
the user; nothing here speaks, clicks or submits on its own.

Order is enforced, not suggested:
    no resume / profile        → ask for the resume (or a manually entered profile)
    profile not confirmed      → show the summary, ask "Is this information correct?"
    preferences never discussed→ ask once, offering resume-based defaults
    search → match → list → details / tailor → apply → answers → CONFIRM → verify
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

from jobs import match as mt
from jobs import profile as pf
from jobs import search as sr
from jobs import tailor as tl
from jobs.apply import ApplicationSession
from jobs.resume import SUPPORTED, UNSUPPORTED_MSG, ResumeError
from jobs.store import UserStore
from jobs.tracker import Tracker

ASK_RESUME = ("Please upload your latest resume first. I'll use it to understand your experience, skills, education, "
              "preferred roles and eligibility before searching for jobs. (PDF, DOCX or TXT — drop it on JUDO, or say "
              "\"create my profile manually\" if you'd rather type it in.)")
ASK_PREFS = ("Resume processed. What type of roles are you looking for? You can also tell me preferred locations, "
             "remote / hybrid / on-site, minimum salary, experience level, whether internships are fine, and India-only "
             "or international remote. If you skip this, I'll search for: {defaults}.")

_services: dict[str, "JobService"] = {}
_lock = threading.Lock()


def for_user(user_id: str | None = None, **kw) -> "JobService":
    """One service per user id — sessions in progress are never shared between users."""
    store = UserStore(user_id)
    with _lock:
        if store.user_id not in _services:
            _services[store.user_id] = JobService(store, **kw)
        return _services[store.user_id]


class JobService:
    def __init__(self, store: UserStore, *, llm=None, browser=None, searcher=None, fetcher=None, confirm_module=None,
                 log=None):
        self.store = store
        self.profiles = pf.Profiles(store)
        self.tracker = Tracker(store)
        self.llm, self.browser = llm, browser
        self.searcher, self.fetcher, self.confirm = searcher, fetcher, confirm_module
        self.log = log or (lambda s: None)
        self.sessions: dict[str, ApplicationSession] = {}
        self.tailored: dict[str, dict] = {}            # job id → {files, approved, text}

    # ── state ──────────────────────────────────────────────────────────────
    def _state(self) -> dict:
        return self.store.load("state", {"asked_prefs": False})

    def _set_state(self, **kw) -> None:
        s = self._state()
        s.update(kw)
        self.store.save("state", s)

    def gate(self) -> str:
        """'' when the user may search; otherwise what to ask first."""
        rec = self.profiles.active()
        if not rec:
            return ASK_RESUME
        if not rec.get("confirmed"):
            return pf.summary(rec)
        return ""

    # ── resume & profile ───────────────────────────────────────────────────
    def upload_resume(self, path: str) -> str:
        if not path:
            return ASK_RESUME
        if not str(path).lower().endswith(SUPPORTED):
            return UNSUPPORTED_MSG
        try:
            rec = self.profiles.add_resume(path, llm=self.llm)
        except ResumeError as e:
            return str(e)
        return pf.summary(rec)

    def create_manual_profile(self, fields: dict) -> str:
        rec = self.profiles.create_manual(fields)
        miss = pf.missing_basics(rec["profile"])
        note = f"\nStill missing: {', '.join(miss)} — applications will ask for these." if miss else ""
        return pf.summary(rec) + note

    def confirm_profile(self) -> str:
        if not self.profiles.active():
            return ASK_RESUME
        self.profiles.confirm()
        if self.profiles.preferences_set():
            return "Thanks — your profile is confirmed. Say \"find jobs\" whenever you're ready."
        self._set_state(asked_prefs=True)
        prefs, defaulted = self.profiles.effective_preferences()
        return ASK_PREFS.format(defaults=pf.describe_preferences(prefs, defaulted))

    def edit_profile(self, changes: dict) -> str:
        try:
            rec = self.profiles.edit(changes)
        except pf.ProfileError as e:
            return str(e)
        return "Updated.\n" + pf.summary(rec)

    def resumes(self) -> str:
        return self.profiles.describe_resumes()

    def select_resume(self, which: str) -> str:
        try:
            rec = self.profiles.select(which)
        except pf.ProfileError as e:
            return str(e)
        return f"Using {rec['name']}." + ("" if rec.get("confirmed") else "\n" + pf.summary(rec))

    def show_profile(self) -> str:
        rec = self.profiles.active()
        return pf.summary(rec) if rec else ASK_RESUME

    # ── preferences ────────────────────────────────────────────────────────
    def set_preferences(self, prefs: dict) -> str:
        if (g := self.gate()):
            return g
        clean = {}
        for k, v in prefs.items():
            if v in (None, "", []):
                continue
            if k in ("roles", "locations", "job_types") and isinstance(v, str):
                v = [x.strip() for x in re.split(r",|/| or | and ", v) if x.strip()]
            if k == "experience_range" and isinstance(v, str):
                nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", v)]
                v = [nums[0], nums[1]] if len(nums) >= 2 else [0, nums[0]] if nums else None
            if k == "remote" and isinstance(v, str):
                v = next((m for m in ("remote", "hybrid", "onsite") if m in v.lower().replace("-", "").replace(" ", "")), "any")
            clean[k] = v
        self.profiles.set_preferences(clean)
        self._set_state(asked_prefs=True)
        prefs, defaulted = self.profiles.effective_preferences()
        return "Got it. I'll search for " + pf.describe_preferences(prefs, defaulted) + "."

    # ── searching ──────────────────────────────────────────────────────────
    def find_jobs(self, fresh_days: int | None = None, roles: list[str] | None = None, progress=None) -> str:
        if (g := self.gate()):
            return g
        st = self._state()
        if not self.profiles.preferences_set() and not st.get("asked_prefs") and not roles:
            self._set_state(asked_prefs=True)
            prefs, defaulted = self.profiles.effective_preferences()
            return ASK_PREFS.format(defaults=pf.describe_preferences(prefs, defaulted))
        prefs, defaulted = self.profiles.effective_preferences()
        if roles:
            prefs = {**prefs, "roles": roles}
        intro = ("Searching fresh " if fresh_days else "Searching ") + pf.describe_preferences(prefs, defaulted) + "."
        jobs = sr.search_jobs(prefs, fresh_days=fresh_days, searcher=self.searcher, fetcher=self.fetcher, progress=progress)
        profile = self.profiles.profile()
        for j in jobs:
            j["match"] = mt.match(j, profile, prefs)
        rank = {mt.ELIGIBLE: 0, mt.POTENTIAL: 1, mt.NOT: 2}
        jobs.sort(key=lambda j: (j["status"] == "expired", rank[j["match"]["classification"]], not j["verified"],
                                 -j["match"]["score"]))
        self.store.save("jobs", {"searched": datetime.now(timezone.utc).isoformat(), "fresh_days": fresh_days,
                                 "prefs": prefs, "jobs": jobs})
        for j in jobs:
            if j["verified"] and j["match"]["classification"] != mt.NOT and j["status"] != "expired":
                if not self.tracker.find(j):
                    self.tracker.upsert(j, "MATCHED")
        return intro + "\n\n" + self.list_jobs()

    def _jobs(self) -> list[dict]:
        return self.store.load("jobs", {}).get("jobs", [])

    def list_jobs(self, show_all: bool = False, limit: int = 12) -> str:
        jobs = self._jobs()
        if not jobs:
            return "No jobs found yet. Try different roles or locations, or ask again later."
        shown = [j for j in jobs if show_all or (j["match"]["classification"] != mt.NOT and j["status"] != "expired")]
        lines = []
        for i, j in enumerate(jobs, 1):
            if j not in shown or len(lines) >= limit:
                continue
            m = j["match"]
            applied = self.tracker.already_applied(j)
            lines.append(f"{i}. {j.get('company') or '?'} — {j.get('title') or '?'} ({j.get('location') or 'location not stated'})"
                         f" · {sr.freshness(j)} · {m['classification']} {m['score']}/100 · {j['source']}"
                         + ("" if j["verified"] else " · page not verified")
                         + (f" · already applied {applied.get('application_date', '')}" if applied else ""))
        hidden = len(jobs) - len(shown)
        tail = (f"\n({hidden} more were not a fit, closed, or couldn't be checked — say \"show all jobs\" to see them.)"
                if hidden and not show_all else "")
        if not lines:
            return "None of the jobs I found fit your profile and filters." + tail
        return "\n".join(lines) + tail + "\nSay \"details of job 1\", \"tailor my resume for job 2\" or \"apply to job 1\"."

    def job(self, which) -> dict | None:
        jobs = self._jobs()
        if str(which).strip().isdigit():
            n = int(which)
            return jobs[n - 1] if 1 <= n <= len(jobs) else None
        w = str(which or "").lower()
        return next((j for j in jobs if j["id"] == which or (w and w in f"{j.get('company', '')} {j.get('title', '')}".lower())), None)

    def details(self, which) -> str:
        j = self.job(which)
        if not j:
            return "Which job? " + self.list_jobs()
        lines = [f"{j.get('title')} — {j.get('company')}",
                 f"Location: {j.get('location') or 'not stated'}   Type: {j.get('employment_type') or 'not stated'}",
                 f"Experience asked: {(j['match']['experience'].get('text') or 'not stated')[:160]}",
                 f"Posted: {sr.freshness(j)}" + (f" (from {j['posted_from']})" if j.get("posted_from") else ""),
                 f"Source: {j['source']}   Job: {j['url']}",
                 f"Apply: {j.get('apply_url') or j['url']}",
                 f"Last verified: {j.get('last_verified', '')[:16].replace('T', ' ')} UTC"
                 + ("" if j["verified"] else f" — couldn't read the page: {j.get('error') or 'unknown'}"),
                 "", mt.explain(j["match"])]
        if j.get("also_on"):
            lines.append("Also listed on: " + ", ".join(x["source"] for x in j["also_on"]))
        return "\n".join(lines)

    # ── tailoring ──────────────────────────────────────────────────────────
    def tailor(self, which) -> str:
        if (g := self.gate()):
            return g
        j = self.job(which)
        if not j:
            return "Which job? " + self.list_jobs()
        rec = self.profiles.active()
        from jobs.resume import read_text
        original = read_text(rec["file"]) if rec.get("file") else ""
        result = tl.tailor(rec["profile"], j)
        problems = tl.verify(original or result["text"], rec["profile"], result["text"]) if original else []
        if problems:
            return "I couldn't make an honest tailored version (" + "; ".join(problems) + "), so I'll keep your original resume."
        files = tl.save(self.store, j, result)
        self.tailored[j["id"]] = {"files": files, "approved": False, "text": result["text"]}
        return ("Here's a version of your resume arranged for this job — nothing added that isn't on your resume:\n- "
                + "\n- ".join(result["changes"] or ["(your resume already fits; only the layout changed)"])
                + f"\n\nSaved: {files.get('docx') or files['txt']}\n\n{result['text'][:3000]}"
                + "\nSay \"use the tailored resume\" to use it for this application, or keep the original.")

    def approve_tailored(self, which) -> str:
        j = self.job(which)
        t = self.tailored.get(j["id"]) if j else None
        if not t:
            return "There's no tailored resume for that job yet — ask me to tailor one first."
        t["approved"] = True
        self.tracker.upsert(j, "RESUME_READY", resume_used=t["files"].get("docx") or t["files"]["txt"])
        return "OK — the tailored resume will be used for this application."

    # ── applying ───────────────────────────────────────────────────────────
    def apply(self, which) -> str:
        if (g := self.gate()):
            return g
        j = self.job(which)
        if not j:
            return "Which job? " + self.list_jobs()
        prior = self.tracker.already_applied(j)
        if prior:
            return f"Already applied on {prior.get('application_date') or 'an earlier date'}. I won't apply again."
        if j["status"] == "expired":
            self.tracker.upsert(j, "EXPIRED")
            return "That posting is closed."
        if j["match"]["classification"] == mt.NOT:
            return ("By your resume this job isn't a fit:\n" + "\n".join("✗ " + b for b in j["match"]["blockers"])
                    + "\nIf you still want to apply, say \"apply anyway to job …\".")
        return self._start(j)

    def apply_anyway(self, which) -> str:
        if (g := self.gate()):
            return g
        j = self.job(which)
        return self._start(j) if j else "Which job? " + self.list_jobs()

    def _start(self, j: dict) -> str:
        rec = self.profiles.active()
        t = self.tailored.get(j["id"])
        resume_file = (t["files"].get("docx") or t["files"]["txt"]) if t and t["approved"] else rec.get("file", "")
        s = ApplicationSession(j, rec["profile"], resume_file, self.tracker, answers=self.profiles.answers(),
                               browser=self.browser, llm=self.llm, confirm_module=self.confirm, log=self.log)
        self.sessions[j["id"]] = s
        return s.prepare()

    def _session(self, which) -> ApplicationSession | None:
        if which in (None, "") and len(self.sessions) == 1:
            return next(iter(self.sessions.values()))
        if which in (None, "") and self.sessions:
            return list(self.sessions.values())[-1]
        j = self.job(which)
        return self.sessions.get(j["id"]) if j else None

    def continue_application(self, which=None) -> str:
        s = self._session(which)
        if not s:
            return "There's no application in progress."
        return s.prepare() if s.state in ("blocked", "new") else s.review_text()

    def answer(self, question, value, which=None, remember: bool = False) -> str:
        s = self._session(which)
        if not s:
            return "There's no application in progress."
        return s.answer(question, value, remember=remember, store_answer=self.profiles.save_answer)

    def submit(self, which=None) -> str:
        """Only puts the review on screen. The application is sent when the user presses CONFIRM."""
        s = self._session(which)
        if not s:
            return "There's no application in progress."
        return s.request_submission()

    def review(self, which=None) -> str:
        s = self._session(which)
        return s.review_text() if s else "There's no application in progress."

    def applications(self) -> str:
        return self.tracker.describe()
