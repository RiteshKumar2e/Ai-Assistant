"""jobs/ — the Job Search & Application Agent.

Live search, job pages and the browser are faked; everything else is the real code.
Resume-based checks run on tests/fixtures/resume_sample.txt (the developer's own
resume, kept out of git) when it is present; multi-user tests also build a second,
made-up resume inline so isolation is tested between two different people.
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobs import apply as ap
from jobs import match as mt
from jobs import resume as rz
from jobs import search as sr
from jobs import tailor as tl
from jobs.service import ASK_RESUME, JobService
from jobs.store import UserStore
from jobs.tracker import Tracker

FIX = Path(__file__).parent / "fixtures"
OWN = FIX / "resume_sample.txt"
TODAY = date(2026, 10, 3)
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

OTHER_RESUME = """PRIYA NAIR
Kochi, India | priya.nair@example.org | +91 91234 56789
github.com/priya-nair-data

EDUCATION
Master of Science in Statistics — Example University   2019 – 2021

EXPERIENCE
Data Analyst — Fabrikam Retail   Aug 2021 – Present
• Built dashboards in Tableau and Power BI
• Wrote SQL on Snowflake and analysis in Pandas

SKILLS
Python, SQL, Pandas, Tableau, Power BI, Excel, Snowflake
"""

needs_own = pytest.mark.skipif(not OWN.exists(), reason="personal resume fixture not present")


def _other(tmp_path) -> Path:
    p = tmp_path / "priya_resume.txt"
    p.write_text(OTHER_RESUME, encoding="utf-8")
    return p


def _own_or_other(tmp_path) -> Path:
    return OWN if OWN.exists() else _other(tmp_path)


# ── fakes ──────────────────────────────────────────────────────────────────

JD_FRESHER = """<h3>About the role</h3><p>Build APIs for payments.</p>
<h3>Requirements</h3><ul><li>0-2 years of experience in backend development</li>
<li>Strong Python and FastAPI</li><li>SQL databases and REST APIs</li>
<li>Bachelor's degree in Computer Science or related field</li></ul>
<h3>Nice to have</h3><ul><li>Docker, Kubernetes</li></ul>"""
JD_SENIOR = """Minimum qualifications:
3+ years of experience in software development with Java.
Experience with distributed systems and Kubernetes.
Bachelor's degree or equivalent practical experience. We build planet-scale infrastructure for everyone."""


class FakeWeb:
    """Search results and job pages for one company job posted on Greenhouse and LinkedIn,
    one senior job on Lever, and a closed posting."""
    gh = "https://boards.greenhouse.io/acme/jobs/4012345"
    li = "https://www.linkedin.com/jobs/view/backend-engineer-at-acme-3999999999?trk=abc"
    lever = "https://jobs.lever.co/globex/0b6a1c2e-1111-2222-3333-444455556666"
    closed = "https://boards.greenhouse.io/initech/jobs/777"

    def __init__(self):
        self.queries = []

    def search(self, query, n, timelimit):
        self.queries.append((query, timelimit))
        if "greenhouse" in query:
            return [{"url": self.gh, "title": "Backend Engineer - Acme"}, {"url": self.closed, "title": "Engineer"}]
        if "linkedin" in query:
            return [{"url": self.li, "title": "Backend Engineer at Acme"}]
        if "lever" in query:
            return [{"url": self.lever, "title": "Software Engineer II"}]
        return []

    def fetch(self, url):
        if "boards-api.greenhouse.io/v1/boards/acme/jobs/4012345" in url:
            return 200, json.dumps({"title": "Backend Engineer", "company_name": "Acme", "location": {"name": "Bengaluru, India"},
                                    "content": html.escape(JD_FRESHER), "first_published": (NOW - timedelta(days=2)).isoformat(),
                                    "absolute_url": self.gh})
        if "boards-api.greenhouse.io/v1/boards/initech" in url:
            return 404, ""
        if "api.lever.co/v0/postings/globex" in url:
            return 200, json.dumps({"text": "Software Engineer II", "categories": {"location": "Hyderabad, India",
                                                                                   "commitment": "Full-time"},
                                    "descriptionPlain": JD_SENIOR, "lists": [], "createdAt": int((NOW - timedelta(days=20)).timestamp() * 1000),
                                    "applyUrl": self.lever + "/apply"})
        if "linkedin.com/jobs/view" in url:
            ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Backend Engineer",
                  "hiringOrganization": {"name": "Acme"}, "description": JD_FRESHER,
                  "jobLocation": {"address": {"addressLocality": "Bengaluru", "addressCountry": "India"}}}
            return 200, f"<html><script type='application/ld+json'>{json.dumps(ld)}</script><body>Backend Engineer</body></html>"
        return 404, ""


class FakeBrowser:
    """A Greenhouse-like application form. `after` is what the page shows after submit."""

    def __init__(self, fields=None, after=None, captcha=False, title="Backend Engineer at Acme"):
        self.calls = []
        self.captcha = captcha
        self.title = title
        self.fields = fields if fields is not None else [
            {"selector": "#first_name", "type": "text", "label": "First Name *", "required": True},
            {"selector": "#last_name", "type": "text", "label": "Last Name *", "required": True},
            {"selector": "#email", "type": "email", "label": "Email *", "required": True},
            {"selector": "#phone", "type": "tel", "label": "Phone", "required": False},
            {"selector": "#resume", "type": "file", "label": "Resume/CV *", "required": True},
            {"selector": "#linkedin", "type": "text", "label": "LinkedIn Profile", "required": False},
            {"selector": "#notice", "type": "text", "label": "What is your notice period? *", "required": True},
            {"selector": "#salary", "type": "text", "label": "Expected CTC *", "required": True},
            {"selector": "#visa", "type": "select", "label": "Will you require visa sponsorship? *", "required": True,
             "options": ["Select...", "Yes", "No"]},
            {"selector": "input[name=\"gender\"]", "type": "radio", "label": "Gender", "required": False,
             "options": ["Male", "Female", "Decline to self-identify"]},
        ]
        self.after = after if after is not None else {
            "url": "https://boards.greenhouse.io/acme/jobs/4012345/confirmation", "title": "Thank you",
            "text": "Thank you for applying! Your application has been submitted.", "fields": 0}

    def call(self, action, params=None):
        self.calls.append((action, params or {}))
        if action == "page_state":
            return {"url": FakeWeb.gh, "title": self.title, "text": f"{self.title}. Apply for this job.",
                    "captcha": self.captcha, "password_fields": 0, "fields": len(self.fields)}
        if action == "read_form":
            return self.fields
        if action == "set_field":
            return "ok"
        if action == "upload_file":
            return "attached:" + Path(params["path"]).name
        if action == "submit_form":
            return {"clicked": "Submit Application", "after": self.after} if self.after != "no-button" else {"clicked": False}
        return "ok"

    def did(self, action):
        return [c for c in self.calls if c[0] == action]


class FakeConfirm:
    """Stands in for core/confirm: remembers the pending callback; only press() runs it."""
    TIMEOUT_SECONDS = 90

    def __init__(self):
        self.pending = None

    def request(self, key, title, detail, run, on_cancel=None):
        self.pending = (title, detail, run, on_cancel)
        return "[CONFIRMATION_PENDING] confirm on screen"

    def press(self, accepted=True):
        title, detail, run, on_cancel = self.pending
        self.pending = None
        return run() if accepted else (on_cancel and on_cancel())


def _service(tmp_path, user="user_a", browser=None, confirm=None):
    web = FakeWeb()
    svc = JobService(UserStore(user, root=tmp_path), searcher=web.search, fetcher=web.fetch,
                     browser=browser or FakeBrowser(), confirm_module=confirm or FakeConfirm())
    return svc, web


def _ready(tmp_path, user="user_a", resume=None, **kw):
    svc, web = _service(tmp_path, user, **kw)
    svc.upload_resume(str(resume or _own_or_other(tmp_path)))
    svc.confirm_profile()
    svc.set_preferences({"roles": "Backend Developer", "locations": "India", "experience_range": "0-2"})
    return svc, web


# ── resume first ───────────────────────────────────────────────────────────

def test_no_resume_means_ask_for_it_and_never_search(tmp_path):
    svc, web = _service(tmp_path)
    assert svc.find_jobs() == ASK_RESUME and "upload your latest resume" in ASK_RESUME
    assert web.queries == []                                       # nothing searched blindly
    assert svc.apply("1") == ASK_RESUME and svc.tailor("1") == ASK_RESUME


def test_unsupported_resume_format_is_explained(tmp_path):
    svc, _ = _service(tmp_path)
    img = tmp_path / "resume.png"
    img.write_bytes(b"\x89PNG")
    out = svc.upload_resume(str(img))
    assert "PDF, DOCX or TXT" in out and svc.profiles.active() is None


@needs_own
def test_parsing_the_developers_own_resume():
    p = rz.parse(rz.read_text(OWN), today=TODAY)
    c = p["candidate"]
    assert c["name"] == "Ritesh Kumar" and c["email"].endswith("@gmail.com") and c["location"] == "Jamshedpur, India"
    assert len(p["experience"]) == 2 and all(e["internship"] for e in p["experience"])
    assert p["experience_months"] == 0 and p["internship_months"] == 21          # internships, not full-time years
    assert p["experience"][0]["company"].startswith("Machine Vision and Intelligence Lab")
    edu = p["education"][0]
    assert edu["degree"].startswith("B.Tech") and edu["graduation_year"] == 2026 and edu["gpa"] == "8.47/10"
    assert edu["field"] == "Computer Science Engineering"
    for s in ("Python", "FastAPI", "Node.js", "TensorFlow", "PyTorch", "OpenCV", "React", "Docker"):
        assert s in p["skills"]
    assert "C" not in p["skills"] and "C++" in p["skills"]                       # "C++" is not "C"
    assert {l["type"] for l in p["links"]} >= {"linkedin", "github", "portfolio"}
    assert len(p["projects"]) == 4 and p["achievements"]


def test_parsing_never_invents_missing_details(tmp_path):
    p = rz.parse("Sam\nSKILLS\nPython, SQL\nEXPERIENCE\nSupport Engineer — Tailwind Traders  Jan 2023 – Dec 2023\n", today=TODAY)
    assert p["candidate"]["email"] == "" and p["candidate"]["phone"] == "" and p["candidate"]["location"] == ""
    assert p["education"] == [] and p["experience_months"] == 12


def test_profile_must_be_confirmed_before_searching(tmp_path):
    svc, web = _service(tmp_path)
    summary = svc.upload_resume(str(_own_or_other(tmp_path)))
    assert summary.startswith("Resume processed successfully.") and "[Looks Correct] [Edit Profile]" in summary
    assert svc.find_jobs().startswith("Resume processed successfully.") and web.queries == []
    ask = svc.confirm_profile()
    assert "What type of roles are you looking for?" in ask                      # preferences come next
    svc.edit_profile({"location": "Kolkata, India"})                             # an edit needs a new confirmation
    assert not svc.profiles.ready()


def test_multiple_resumes_can_be_chosen(tmp_path):
    svc, _ = _service(tmp_path)
    svc.upload_resume(str(_own_or_other(tmp_path)))
    svc.upload_resume(str(_other(tmp_path)))
    assert "[1]" in svc.resumes() and "[2]" in svc.resumes()
    svc.select_resume("1")
    assert svc.profiles.active()["file"].endswith(_own_or_other(tmp_path).name)


# ── isolation ──────────────────────────────────────────────────────────────

def test_users_never_see_each_others_data(tmp_path):
    a, _ = _ready(tmp_path, "user_a")
    b, _ = _service(tmp_path, "user_b")
    assert b.find_jobs() == ASK_RESUME                       # user B has no resume — A's is not borrowed
    b.upload_resume(str(_other(tmp_path)))
    assert b.profiles.profile()["candidate"]["name"] == "Priya Nair"
    assert a.profiles.profile()["candidate"]["name"] != "Priya Nair"
    a.find_jobs()
    Tracker(a.store).upsert(a.job("1"), "SUBMITTED")
    assert Tracker(b.store).all() == [] and b._jobs() == []
    assert not b.store.owns(a.profiles.active()["file"])
    with pytest.raises(ValueError):
        UserStore("../user_a", root=tmp_path)


# ── matching ───────────────────────────────────────────────────────────────

def _profile(tmp_path):
    return rz.parse(rz.read_text(_own_or_other(tmp_path)), today=TODAY)


def test_fresher_job_is_eligible_with_reasons(tmp_path):
    m = mt.match({"title": "Backend Engineer", "location": "Bengaluru, India", "description": JD_FRESHER},
                 _profile(tmp_path) if OWN.exists() else rz.parse(rz.read_text(OWN), today=TODAY) if OWN.exists() else
                 {**_profile(tmp_path), "experience_months": 0, "skills": ["Python", "FastAPI", "PostgreSQL", "REST APIs"]},
                 {"locations": ["India"], "experience_range": [0, 2]})
    assert m["classification"] == mt.ELIGIBLE
    matched = [s for s, _ in m["matched_required"]]
    assert {"Python", "FastAPI", "REST APIs"} <= set(matched) and "Kubernetes" in m["missing_preferred"]
    text = mt.explain(m)
    assert "MATCHED:" in text and "EXPERIENCE: 0–2 years required" in text and "EDUCATION: Bachelor's required — you meet it" in text


def test_experience_rules(tmp_path):
    p = {**_profile(tmp_path), "experience_months": 0, "internship_months": 6}
    senior = mt.match({"title": "Software Engineer II", "location": "Hyderabad, India", "description": JD_SENIOR}, p, {"locations": ["India"]})
    assert senior["classification"] == mt.NOT and any("3+ years" in b for b in senior["blockers"])
    pref = mt.match({"title": "Developer", "location": "Pune, India",
                     "description": "Requirements\nPython and SQL.\n1-3 years preferred experience in web development.\n" * 3},
                    p, {"locations": ["India"]})
    assert pref["classification"] == mt.POTENTIAL                     # preferred, not required → maybe
    filt = mt.match({"title": "Engineer", "location": "Pune, India", "description": "Requirements\n2-4 years of experience with Python.\n" * 4},
                    {**p, "experience_months": 36}, {"locations": ["India"], "experience_range": [0, 2]})
    assert filt["classification"] == mt.NOT and any("0–2 years filter" in b for b in filt["blockers"])   # the user's own filter


def test_location_and_authorisation_are_judged_only_from_known_facts(tmp_path):
    p = _profile(tmp_path)
    us = mt.match({"title": "ML Engineer", "location": "Remote - United States",
                   "description": ("Requirements\nPython, PyTorch.\nMust be authorized to work in the US; we are unable to sponsor visas.\n") * 3},
                  p, {"locations": ["India"]})
    assert us["classification"] == mt.NOT and "USER INPUT REQUIRED" in us["authorization"]


# ── search, freshness, duplicates ──────────────────────────────────────────

def test_search_reads_real_pages_dedupes_and_marks_closed(tmp_path):
    svc, web = _ready(tmp_path)
    out = svc.find_jobs(fresh_days=7)
    jobs = svc._jobs()
    acme = [j for j in jobs if j["company"] == "Acme"]
    assert len(acme) == 1 and acme[0]["source"] == "Greenhouse"                 # LinkedIn copy merged into the ATS one
    assert acme[0]["also_on"][0]["source"] == "LinkedIn" and acme[0]["verified"]
    assert acme[0]["posted_from"].startswith("Greenhouse") and "posted 2 days ago" in sr.freshness(acme[0], NOW)
    assert all(j["company"] != "Globex" for j in jobs)                          # posted 20 days ago → not "fresh"
    assert any(q[1] == "w" for q in web.queries)                                # searched with a recency filter
    assert "Acme — Backend Engineer" in out and "1." in out


def test_unknown_posting_dates_are_never_called_fresh():
    job = {"posted_at": None}
    assert sr.freshness(job) == "posting date unknown" and not sr.is_fresh(job)
    assert sr._posted_from_text("Posted 3 days ago · 120 applicants", NOW).startswith("2026-09-30")
    assert sr.normalize_url("https://www.linkedin.com/jobs/view/123/?trk=x&refId=y") == "https://linkedin.com/jobs/view/123"


# ── applying ───────────────────────────────────────────────────────────────

def _applying(tmp_path, **kw):
    browser = kw.pop("browser", None) or FakeBrowser()
    confirm = FakeConfirm()
    svc, web = _ready(tmp_path, browser=browser, confirm=confirm, **kw)
    svc.find_jobs()
    n = next(str(i) for i, j in enumerate(svc._jobs(), 1) if j["company"] == "Acme")
    return svc, browser, confirm, n


def test_unknown_and_sensitive_questions_are_never_guessed(tmp_path):
    svc, browser, confirm, n = _applying(tmp_path)
    review = svc.apply(n)
    filled = {c[1]["selector"]: c[1]["value"] for c in browser.did("set_field")}
    assert "#email" in filled and "#first_name" in filled
    assert not {"#notice", "#salary", "#visa", 'input[name="gender"]'} & set(filled)   # left for the user
    assert browser.did("upload_file")                                                  # the user's own resume attached
    assert "Questions requiring attention:" in review and "notice period" in review.lower()
    assert "never guessed" in review                                                   # the gender question
    assert "required questions" in svc.submit(n) and confirm.pending is None           # can't even ask yet


def test_submission_needs_an_explicit_confirmation_event(tmp_path):
    svc, browser, confirm, n = _applying(tmp_path)
    svc.apply(n)
    for q, a in (("notice", "30 days"), ("expected", "6 LPA"), ("visa", "No")):
        svc.answer(q, a, n)
    s = svc.sessions[svc.job(n)["id"]]
    assert s.state == "ready"
    with pytest.raises(PermissionError):
        s._submit()                                          # no confirmation → cannot submit, even from inside
    out = svc.submit(n)
    assert out.startswith("[CONFIRMATION_PENDING]") and not browser.did("submit_form")
    title, detail, run, cancel = confirm.pending
    assert "APPLICATION REVIEW" in detail and "Company: Acme" in detail and "Questions requiring attention" in detail
    assert s._confirmed("forged-token").startswith("That confirmation is no longer valid") and not browser.did("submit_form")
    result = confirm.press(True)                             # the human presses CONFIRM & SUBMIT
    assert browser.did("submit_form") and "verified" in result.lower()
    rec = svc.tracker.find(svc.job(n))
    assert rec["status"] == "VERIFIED" and "Thank you for applying" in rec["verification_evidence"]
    assert run() .startswith("That confirmation is no longer valid")                  # single use
    assert len(browser.did("submit_form")) == 1


def test_cancel_or_changes_after_review_submit_nothing(tmp_path):
    svc, browser, confirm, n = _applying(tmp_path)
    svc.apply(n)
    for q, a in (("notice", "30 days"), ("expected", "6 LPA"), ("visa", "No")):
        svc.answer(q, a, n)
    svc.submit(n)
    confirm.press(False)                                     # CANCEL
    assert not browser.did("submit_form") and svc.sessions[svc.job(n)["id"]].state == "ready"
    svc.submit(n)
    _, _, run, _ = confirm.pending
    svc.answer("expected", "7 LPA", n)                       # edited after the review was shown
    assert "nothing was submitted" in run() or "didn't submit" in run() or not browser.did("submit_form")
    assert not browser.did("submit_form")


def test_unverified_and_failed_submissions_are_reported_honestly(tmp_path):
    for after, status, phrase in (({"url": "https://x/jobs/1", "title": "Acme", "text": "Acme careers", "fields": 0},
                                   "SUBMITTED", "SUBMISSION UNVERIFIED"),
                                  ({"url": "https://x/jobs/1", "title": "Apply", "text": "Phone: This field is required",
                                    "fields": 9}, "FAILED", "didn't accept"),
                                  ("no-button", "FAILED", "submit button")):
        sub = tmp_path / status / str(len(phrase))
        sub.mkdir(parents=True)
        svc, browser, confirm, n = _applying(sub, browser=FakeBrowser(after=after))
        svc.apply(n)
        for q, a in (("notice", "30 days"), ("expected", "6 LPA"), ("visa", "No")):
            svc.answer(q, a, n)
        svc.submit(n)
        out = confirm.press(True)
        assert phrase in out and svc.tracker.find(svc.job(n))["status"] == status
        assert "verified" not in out.lower() or status == "VERIFIED"


def test_duplicate_applications_are_refused(tmp_path):
    svc, browser, confirm, n = _applying(tmp_path)
    svc.tracker.upsert(svc.job(n), "VERIFIED", verification_evidence="Thank you for applying")
    out = svc.apply(n)
    assert out.startswith("Already applied on") and not browser.did("go_to")
    # the same job reached through LinkedIn is still the same application
    li = {"url": FakeWeb.li, "company": "Acme", "title": "Backend Engineer", "source": "LinkedIn"}
    assert svc.tracker.already_applied(li)


def test_captcha_stops_the_agent(tmp_path):
    svc, browser, confirm, n = _applying(tmp_path, browser=FakeBrowser(captcha=True))
    out = svc.apply(n)
    assert out.startswith("CAPTCHA detected. Please complete it manually") and not browser.did("set_field")


def test_wrong_page_is_not_filled(tmp_path):
    svc, browser, confirm, n = _applying(tmp_path, browser=FakeBrowser(title="Senior Data Scientist at Contoso"))
    assert "doesn't look like" in svc.apply(n) and not browser.did("read_form")


# ── tailoring ──────────────────────────────────────────────────────────────

def test_tailored_resume_reorders_but_never_invents(tmp_path):
    p = _profile(tmp_path)
    original = rz.read_text(_own_or_other(tmp_path))
    job = {"id": "j1", "title": "Backend Engineer", "company": "Acme",
           "description": JD_FRESHER + "<h3>Requirements</h3><ul><li>Kubernetes and Go</li></ul>"}
    out = tl.tailor(p, job)
    assert tl.verify(original, p, out["text"]) == []
    assert "Kubernetes" not in out["text"] and any("Not added" in c for c in out["changes"])
    assert tl.verify(original, p, out["text"] + "\n• Led a team of 40 engineers using Kubernetes\n")   # caught
    svc, _ = _ready(tmp_path)
    svc.find_jobs()
    n = next(str(i) for i, j in enumerate(svc._jobs(), 1) if j["company"] == "Acme")
    shown = svc.tailor(n)
    assert "nothing added that isn't on your resume" in shown and svc.tailored[svc.job(n)["id"]]["approved"] is False
    assert svc.store.owns(svc.tailored[svc.job(n)["id"]]["files"]["txt"])


# ── wiring ─────────────────────────────────────────────────────────────────

def test_plugin_asks_for_resume_first_and_has_no_direct_submit(tmp_path, monkeypatch):
    import jobs.service as service
    import plugins.job_agent as plugin
    monkeypatch.setattr(service, "for_user", lambda **kw: _service(tmp_path, "plugin_user")[0])
    monkeypatch.setattr(plugin, "_service", lambda player: service.for_user())
    assert plugin.run({"action": "find_jobs"}) == ASK_RESUME
    assert "only shows the review" in plugin.PLUGIN["description"]


def test_job_agent_is_registered_with_the_supervisor():
    from agent.runtime import AGENTS
    from agent.agents.jobs import JobAgent
    assert JobAgent in AGENTS
    names = {t.name for t in JobAgent(cc=None).tools()}
    assert {"find_jobs", "prepare_application", "request_submission"} <= names and "submit_application" not in names
