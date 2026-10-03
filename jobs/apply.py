"""
jobs/apply.py — fill a real job application in JUDO Browser, stop for what only the
user can answer, and submit ONLY after the user presses CONFIRM on screen.

    prepare()  open the application page → verify it's this job and still open →
               stop on CAPTCHA / login → read the form → fill what the user's verified
               profile answers → attach the chosen resume → list what needs the user
    answer()   the user's own answers (notice period, salary, visa…, or a sensitive
               question they choose to answer) → filled in
    request_submission()
               the APPLICATION REVIEW goes up as a core/confirm banner. Nothing else.

THE GATE
    Submitting is _submit(), which refuses to run unless _gate_open is set — and the
    only code that sets it is the callback core/confirm runs when the human presses
    CONFIRM, after checking the single-use token minted for that one review. No tool,
    plugin action or model output can reach _submit(): the model can only *ask* for
    the banner (request_submission), exactly like core/confirm's other irreversible
    actions. Any change to the form after the review was shown invalidates it.

AFTER SUBMITTING
    The page is read again. Only a success message, confirmation number, or "application
    received" page counts as VERIFIED; a validation error is FAILED; anything else is
    SUBMITTED with "SUBMISSION UNVERIFIED" — JUDO never says "applied" without evidence.
"""
from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass, field
from typing import Callable

from jobs import skills as sk
from jobs.search import EXPIRED
from jobs.tracker import Tracker

# ── what a form field is asking for ────────────────────────────────────────
PROFILE, SAVED, GENERATED, USER, SENSITIVE, FILE, SKIP = "profile", "saved", "generated", "user", "sensitive", "file", "skip"

FIELD_RULES: list[tuple[str, str]] = [  # (category, label regex) — first match wins
    ("resume", r"\b(?:resume|cv|curriculum vitae)\b"),
    ("cover_letter", r"cover\s*letter"),
    ("sensitive", r"\bgender\b|\bsex\b|pronoun|\brace\b|ethnic|hispanic|latin[oa]|veteran|disabilit|sexual orientation|"
                  r"transgender|religio|\bcaste\b|marital|date of birth|\bdob\b|\bage\b|nationality|citizenship status"),
    ("work_authorization", r"authori[sz]ed to work|work authori[sz]ation|right to work|legally (?:eligible|permitted)|work permit"),
    ("visa_sponsorship", r"sponsor|\bvisa\b|h-?1b"),
    ("notice_period", r"notice period|how soon can you join|joining time|earliest (?:start|joining)|available to (?:start|join)|start date|availability"),
    ("expected_salary", r"expected (?:salary|ctc|compensation|pay)|salary expectation|desired (?:salary|pay|compensation)|compensation expectation"),
    ("current_salary", r"current (?:salary|ctc|compensation)|present ctc"),
    ("relocation", r"relocat"),
    ("consent", r"\bi (?:agree|accept|consent|acknowledge|certify|confirm)|privacy (?:policy|notice)|terms (?:and|&) conditions|"
                r"by (?:checking|submitting)|data processing"),
    ("referral", r"referr|referred by|employee who referred"),
    ("heard_about", r"how did you (?:hear|find|learn)|where did you (?:hear|find|see)|source of application"),
    ("first_name", r"\bfirst name\b|\bgiven name\b|\bfname\b"),
    ("last_name", r"\blast name\b|\bsurname\b|\bfamily name\b|\blname\b"),
    ("full_name", r"\bfull name\b|^\s*name\s*\*?\s*$|\byour name\b|\bcandidate name\b|^name\b"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile|contact number|\bcell\b"),
    ("linkedin", r"linked\s*in"),
    ("github", r"github|gitlab"),
    ("portfolio", r"portfolio|personal (?:website|site)|\bwebsite\b|\bblog\b|other (?:website|link)"),
    ("location", r"current (?:location|city)|^\s*location|\bcity\b|where are you (?:located|based)|address"),
    ("current_company", r"current (?:company|employer|organi[sz]ation)|present employer"),
    ("current_title", r"current (?:title|role|designation|position)|job title"),
    ("years_experience", r"years of (?:professional |relevant |total |work )?experience|total experience|how many years"),
    ("school", r"school|university|college|institution"),
    ("degree", r"\bdegree\b|qualification"),
    ("graduation_year", r"graduation (?:year|date)|year of (?:graduation|passing)|passing year"),
    ("gpa", r"\bc?gpa\b|\bcpi\b|grade point"),
    ("open_question", r"why (?:do you|are you|this)|tell us|describe|what (?:interests|excites|makes)|anything else|additional information|cover note"),
]
NEEDS_USER = {"work_authorization", "visa_sponsorship", "notice_period", "expected_salary", "current_salary", "relocation",
              "consent", "referral", "heard_about"}
ATTENTION_NAMES = {"work_authorization": "Work authorisation", "visa_sponsorship": "Visa sponsorship",
                   "notice_period": "Notice period / start date", "expected_salary": "Expected salary",
                   "current_salary": "Current salary", "relocation": "Relocation", "consent": "Consent / declaration",
                   "referral": "Referral", "heard_about": "How you heard about the job", "sensitive": "Voluntary / demographic question",
                   "open_question": "Written answer", "unknown": "Question"}
SUCCESS = re.compile(r"(?i)(?:application (?:has been |was )?(?:successfully )?(?:submitted|received|sent|complete)|thank(?:s| you) for (?:applying|your application|your interest)|"
                     r"we(?:'ve| have) received your application|your application (?:is|has been) (?:in|submitted|received)|"
                     r"successfully applied|application submitted|you(?:'ve| have) applied)")
CONFIRM_NO = re.compile(r"(?i)(?:confirmation|reference|application|tracking)\s*(?:number|no\.?|id|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})")
FORM_ERROR = re.compile(r"(?i)(?:this field is required|is required|please (?:fill|complete|correct|enter|select|upload)|"
                        r"there (?:was|were) (?:an )?errors?|invalid (?:email|phone|value)|could not be submitted|failed to submit)")


def classify(label: str, ftype: str = "", name: str = "", autocomplete: str = "") -> str:
    text = f"{label} {name.replace('_', ' ')} {autocomplete.replace('-', ' ')}".strip().lower()
    if ftype == "file":
        if re.search(r"cover", text):
            return "cover_letter"
        return "resume" if re.search(r"resume|cv|attach|upload|document", text) or not text else "file_other"
    for cat, pat in FIELD_RULES:
        if re.search(pat, text, re.I):
            if cat in ("resume", "cover_letter") and ftype not in ("file", "textarea"):
                continue
            return cat
    if ftype == "textarea":
        return "open_question"
    return "unknown"


@dataclass
class Item:
    """One field of the form and what JUDO intends to do with it."""
    n: int
    selector: str
    label: str
    ftype: str
    required: bool
    category: str
    options: list = field(default_factory=list)
    value: str = ""
    source: str = ""        # profile | saved | generated | user | file | skip
    state: str = "pending"  # filled | needs_input | sensitive | skipped | failed | attached
    note: str = ""


class BrowserPort:
    """JUDO Browser through its existing remote control (judo_browser/control.py)."""

    def call(self, action: str, params: dict | None = None):
        from judo_browser import client
        return client.send(action, params or {}, timeout=90)


class ApplicationSession:
    def __init__(self, job: dict, profile: dict, resume_file: str, tracker: Tracker, *, answers: dict | None = None,
                 browser=None, llm=None, confirm_module=None, log: Callable[[str], None] | None = None):
        self.job, self.profile, self.resume_file = job, profile, resume_file
        self.tracker, self.saved = tracker, dict(answers or {})
        self.browser = browser or BrowserPort()
        self.llm = llm
        self.confirm = confirm_module
        self.log = log or (lambda s: None)
        self.items: list[Item] = []
        self.state = "new"          # new | blocked | needs_input | ready | waiting | submitted | verified | failed | expired
        self.message = ""
        self._gate_open = False
        self._token: str | None = None
        self._reviewed: tuple | None = None
        self._lock = threading.Lock()

    # ── 1. open, verify, read, fill ───────────────────────────────────────
    def prepare(self) -> str:
        prior = self.tracker.already_applied(self.job)
        if prior:
            self.state = "blocked"
            return self._say(f"Already applied on {prior.get('application_date') or 'an earlier date'} "
                             f"({prior['status']}). I won't apply again.")
        url = self.job.get("apply_url") or self.job.get("url")
        self.browser.call("go_to", {"url": url})
        st = self._page()
        if (blocked := self._blocked(st)):
            return blocked
        if not self._is_this_job(st):
            self.state = "blocked"
            return self._say(f"The page that opened ({st.get('title') or st.get('url')}) doesn't look like "
                             f"{self.job.get('title')} at {self.job.get('company')}, so I stopped. Please check the link.")
        if not st.get("fields"):
            # many job pages show the form only after "Apply"
            self.browser.call("click", {"text": "Apply"})
            st = self._page()
            if (blocked := self._blocked(st)):
                return blocked
        form = self.browser.call("read_form", {}) or []
        if not form:
            self.state = "blocked"
            return self._say("I couldn't find an application form on this page. It may need you to sign in or "
                             "use the company's own flow — it's open in JUDO Browser.")
        self.tracker.upsert(self.job, "APPLICATION_STARTED", resume_used=self._resume_name())
        self.items = [self._plan(i, f) for i, f in enumerate(form, 1)]
        self._fill_all()
        return self._after_change()

    def _page(self) -> dict:
        st = self.browser.call("page_state", {}) or {}
        return st if isinstance(st, dict) else {}

    def _blocked(self, st: dict) -> str:
        if st.get("captcha"):
            self.state = "blocked"
            return self._say("CAPTCHA detected. Please complete it manually in JUDO Browser, then tell me to continue.")
        text = st.get("text", "")
        if EXPIRED.search(text[:4000]):
            self.state = "expired"
            self.tracker.upsert(self.job, "EXPIRED")
            return self._say("This job is no longer accepting applications.")
        if st.get("password_fields") and (st.get("fields") or 0) <= 4:
            self.state = "blocked"
            return self._say("This site needs you to sign in first. Please log in yourself in JUDO Browser "
                             "(I never type passwords), then tell me to continue.")
        return ""

    def _is_this_job(self, st: dict) -> bool:
        hay = re.sub(r"[^a-z0-9]+", " ", f"{st.get('title', '')} {st.get('text', '')[:6000]} {st.get('url', '')}".lower())
        words = [w for w in re.sub(r"[^a-z0-9]+", " ", (self.job.get("title") or "").lower()).split() if len(w) > 2]
        company = re.sub(r"[^a-z0-9]+", " ", (self.job.get("company") or "").lower()).strip()
        title_ok = not words or sum(w in hay for w in words) >= max(1, (len(words) + 1) // 2)
        company_ok = not company or company in hay or company.replace(" ", "") in hay.replace(" ", "")
        return title_ok and company_ok

    # ── mapping fields to the profile ─────────────────────────────────────
    def _profile_value(self, cat: str) -> str:
        p, c = self.profile, self.profile.get("candidate", {})
        name = (c.get("name") or "").split()
        links = {l["type"]: l["url"] for l in p.get("links", [])}
        current = next((e for e in p.get("experience", []) if e.get("current")), None)
        edu = (p.get("education") or [{}])[0]
        return {
            "first_name": name[0] if name else "", "last_name": " ".join(name[1:]) if len(name) > 1 else "",
            "full_name": c.get("name", ""), "email": c.get("email", ""), "phone": c.get("phone", ""),
            "location": c.get("location", ""), "linkedin": links.get("linkedin", ""),
            "github": links.get("github", "") or links.get("gitlab", ""), "portfolio": links.get("portfolio", ""),
            "current_company": current.get("company", "") if current else "",
            "current_title": current.get("title", "") if current else "",
            "years_experience": (f"{p.get('experience_months', 0) / 12:g}" if "experience_months" in p else ""),
            "school": edu.get("institution", ""), "degree": edu.get("degree", ""),
            "graduation_year": str(edu.get("graduation_year") or ""), "gpa": edu.get("gpa", ""),
            "work_authorization": c.get("work_authorization", ""),
        }.get(cat, "")

    def _plan(self, n: int, f: dict) -> Item:
        cat = classify(f.get("label", ""), f.get("type", ""), f.get("name", ""), f.get("autocomplete", ""))
        it = Item(n, f.get("selector", ""), f.get("label", "") or f.get("name", ""), f.get("type", ""),
                  bool(f.get("required")), cat, list(f.get("options") or []))
        if f.get("value") and f.get("type") != "file":
            it.value, it.source, it.state = str(f["value"]), "page", "filled"     # already filled (site remembered it)
            return it
        if cat == "resume":
            it.source, it.value = FILE, self.resume_file
        elif cat in ("cover_letter", "file_other"):
            it.source, it.state = USER, ("needs_input" if it.required else "skipped")
            it.note = "a file only you can provide"
        elif cat == "sensitive":
            it.source, it.state = SENSITIVE, "sensitive"
            it.note = "voluntary — never guessed from your resume; answer it yourself or leave it"
            if self.saved.get(f"sensitive:{_key(it.label)}"):
                it.value, it.source, it.state = self.saved[f"sensitive:{_key(it.label)}"], SAVED, "pending"
        elif cat in NEEDS_USER or cat == "unknown":
            stored = self.saved.get(cat) if cat in NEEDS_USER else None
            if not stored and cat == "work_authorization":
                stored = self._profile_value(cat)
            if stored:
                it.value, it.source = stored, SAVED
            elif it.required:
                it.source, it.state = USER, "needs_input"
            else:
                it.source, it.state = SKIP, "skipped"
        elif cat == "open_question":
            ans = self._generate(it.label) if it.required or self.llm else ""
            if ans:
                it.value, it.source, it.note = ans, GENERATED, "written from your resume — please read it"
            elif it.required:
                it.source, it.state = USER, "needs_input"
            else:
                it.source, it.state = SKIP, "skipped"
        else:
            v = self._profile_value(cat)
            if v:
                it.value, it.source = v, PROFILE
            elif it.required:
                it.source, it.state = USER, "needs_input"
                it.note = "not on your profile"
            else:
                it.source, it.state = SKIP, "skipped"
        if it.value and it.options and it.state == "pending":
            choice = _pick_option(it.value, it.options)
            if choice:
                it.value = choice
            else:   # the site's choices don't contain the profile's wording — the user picks
                it.note = f"options: {', '.join(it.options[:8])}"
                it.value, it.source, it.state = "", USER, ("needs_input" if it.required else "skipped")
        return it

    def _generate(self, question: str) -> str:
        """A short answer built only from the resume's facts; rejected if it introduces
        skills that aren't on the resume."""
        if self.llm is None:
            return ""
        p = self.profile
        facts = {"skills": p.get("skills", [])[:25], "experience": [{k: e.get(k) for k in ("title", "company", "start", "end", "details")}
                                                                    for e in p.get("experience", [])[:4]],
                 "projects": [{"name": x["name"], "details": x.get("details", [])[:3]} for x in p.get("projects", [])[:4]],
                 "education": [e.get("raw", "") for e in p.get("education", [])[:2]]}
        prompt = (f"Write a short (60-120 words), first-person answer to this job application question for the role "
                  f"{self.job.get('title')} at {self.job.get('company')}. Use ONLY these facts from the candidate's resume; "
                  f"do not invent experience, skills, numbers or achievements.\nQUESTION: {question}\nFACTS: {facts}\n"
                  "Reply with the answer text only.")
        try:
            text = (self.llm.complete(prompt) or "").strip()
        except Exception:
            return ""
        allowed = set(sk.find_skills(str(facts))) | set(p.get("skills", []))
        if not text or set(sk.find_skills(text)) - allowed:
            return ""
        return text[:1500]

    # ── filling ───────────────────────────────────────────────────────────
    def _fill_all(self) -> None:
        for it in self.items:
            if it.state == "pending" and (it.value or it.source == FILE):
                self._fill(it)

    def _fill(self, it: Item) -> None:
        if it.source == FILE:
            r = str(self.browser.call("upload_file", {"selector": it.selector, "path": it.value}) or "")
            it.state = "attached" if r.startswith("attached") else "failed"
            it.note = "" if it.state == "attached" else "the resume didn't attach — please attach it yourself"
            return
        r = self.browser.call("set_field", {"selector": it.selector, "value": it.value, "type": it.ftype})
        it.state = "filled" if r == "ok" else "failed"
        if it.state == "failed":
            it.note = f"couldn't fill it ({r})"

    def questions(self) -> list[Item]:
        """What the user still has to look at: required unknowns, failures, sensitive questions."""
        return [it for it in self.items if it.state in ("needs_input", "failed", "sensitive")]

    def answer(self, which: str | int, value: str, remember: bool = False, store_answer: Callable | None = None) -> str:
        """The user's own answer to question `which` (its number or words from its label).
        'skip' leaves an optional / sensitive question empty."""
        it = self._find(which)
        if not it:
            return self._say("Which question? " + self._question_list())
        if str(value).strip().lower() in ("skip", "leave it", "prefer not to say", "") and not it.required:
            it.state, it.value, it.source = "skipped", "", SKIP
            return self._after_change()
        if it.options:
            choice = _pick_option(value, it.options)
            if not choice:
                return self._say(f"For “{it.label}” the choices are: {', '.join(it.options)}.")
            value = choice
        it.value, it.source, it.state = str(value), USER, "pending"
        self._fill(it)
        if remember and store_answer and it.category in NEEDS_USER:
            store_answer(it.category, str(value))
        if remember and store_answer and it.category == "sensitive":
            store_answer(f"sensitive:{_key(it.label)}", str(value))
        return self._after_change()

    def _find(self, which) -> Item | None:
        if str(which).strip().isdigit():
            return next((i for i in self.items if i.n == int(which)), None)
        w = str(which).lower()
        return next((i for i in self.items if w and w in i.label.lower()), None)

    def _after_change(self) -> str:
        self._reviewed = None                     # anything changed → the old review is void
        if self.state in ("waiting",):
            self._token = None
        blocking = [it for it in self.items if it.required and it.state in ("needs_input", "failed")]
        self.state = "needs_input" if blocking else "ready"
        return self._say(self.review_text())

    def _question_list(self) -> str:
        qs = self.questions()
        return "; ".join(f"[{i.n}] {i.label[:70]}" for i in qs) or "nothing is waiting for you."

    # ── 2. the review and the gate ────────────────────────────────────────
    def review_text(self) -> str:
        j = self.job
        filled = [i for i in self.items if i.state in ("filled", "attached")]
        att = self.questions()
        lines = ["APPLICATION REVIEW", "",
                 f"Company: {j.get('company') or '?'}", f"Role: {j.get('title') or '?'}", f"Location: {j.get('location') or 'not stated'}",
                 "", f"Resume: {self._resume_name()}" + ("" if any(i.category == "resume" and i.state == "attached" for i in self.items)
                                                        else " (not attached yet)" if any(i.category == "resume" for i in self.items)
                                                        else " (this form has no resume upload)"),
                 "", "Fields filled:"]
        lines += [f"  {i.label[:60]}: {_mask(i)}" for i in filled if i.source in (PROFILE, "page", FILE)] or ["  none"]
        answered = [i for i in filled if i.source in (USER, SAVED, GENERATED)]
        lines += ["", "Questions answered:"] + ([f"  {i.label[:60]}: {i.value[:120]}" + (" (written from your resume — check it)"
                                                                                      if i.source == GENERATED else "")
                                                for i in answered] or ["  none"])
        lines += ["", "Questions requiring attention:"] + ([f"  [{i.n}] {i.label[:80]}" + (" (required)" if i.required else "")
                                                         + (f" — {i.note}" if i.note else "")
                                                         + (f" — {ATTENTION_NAMES.get(i.category, '')}" if i.category in ATTENTION_NAMES else "")
                                                         for i in att] or ["  none"])
        if self.state == "needs_input":
            lines += ["", "Answer the required questions above before this can be submitted."]
        return "\n".join(lines)

    def _snapshot(self) -> tuple:
        return tuple((i.selector, i.value, i.state) for i in self.items)

    def request_submission(self) -> str:
        """Put the review on screen with CONFIRM & SUBMIT / CANCEL. Never submits by itself."""
        if self.state == "needs_input":
            return self._say("Some required questions still need your answer: " + self._question_list())
        if self.state != "ready":
            return self._say(f"This application can't be submitted right now ({self.state}). {self.message}")
        st = self._page()
        if st.get("captcha"):
            return self._say("CAPTCHA detected. Please complete it manually in JUDO Browser, then ask me to submit again.")
        confirm = self.confirm
        if confirm is None:
            from core import confirm
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._token, self._reviewed = token, self._snapshot()
        self.state = "waiting"
        self.tracker.upsert(self.job, "WAITING_FOR_CONFIRMATION")
        title = f"Submit application — {self.job.get('title', 'job')} at {self.job.get('company', '?')}"

        def on_cancel():
            with self._lock:
                if self._token == token:
                    self._token = None
                    self.state = "ready"
            self.log("SYS: Application not submitted (cancelled).")
        return confirm.request(f"job_apply:{self.job.get('id', '')}", title, self.review_text() +
                               "\n\n[CONFIRM & SUBMIT] [CANCEL]", lambda: self._confirmed(token), on_cancel=on_cancel)

    def _confirmed(self, token: str) -> str:
        """Runs only from core/confirm.resolve(True) — the human pressed CONFIRM."""
        with self._lock:
            ok = self._token is not None and secrets.compare_digest(self._token, token)
            self._token = None                          # single use
            unchanged = self._reviewed == self._snapshot()
        if not ok:
            return "That confirmation is no longer valid — nothing was submitted."
        if not unchanged:
            self.state = "ready"
            return "The application changed after the review, so I didn't submit. Please review it again."
        self._gate_open = True
        try:
            return self._submit()
        finally:
            self._gate_open = False

    def _submit(self) -> str:
        if not self._gate_open:
            raise PermissionError("An application can only be submitted after the user confirms on screen.")
        st = self._page()
        if st.get("captcha"):
            self.state = "ready"
            return self._say("CAPTCHA detected on submit. Please complete it manually, then ask me to submit again.")
        res = self.browser.call("submit_form", {}) or {}
        if not res.get("clicked"):
            self.state = "failed"
            self.tracker.upsert(self.job, "FAILED", notes="couldn't find the submit button")
            return self._say("I couldn't find the form's submit button, so nothing was sent. It's open in JUDO Browser.")
        return self._verify(res.get("after") or {})

    # ── 3. evidence ───────────────────────────────────────────────────────
    def _verify(self, after: dict) -> str:
        text, url = after.get("text", "") or "", after.get("url", "") or ""
        success, number = SUCCESS.search(text), CONFIRM_NO.search(text)
        if success or number or re.search(r"(?i)/(?:thank[-_]?you|confirmation|application[-_]?submitted|success)\b", url):
            evidence = "; ".join(x for x in (success.group(0) if success else "",
                                             f"confirmation number {number.group(1)}" if number else "",
                                             f"page {url}" if url else "") if x)
            self.state = "verified"
            self.tracker.upsert(self.job, "VERIFIED", verification_evidence=evidence)
            return self._say(f"Application submitted and verified — {evidence}.")
        if FORM_ERROR.search(text) and (after.get("fields") or 0) > 0:
            err = FORM_ERROR.search(text).group(0)
            self.state = "failed"
            self.tracker.upsert(self.job, "FAILED", notes=f"form error: {err}")
            return self._say(f"The site didn't accept the application (“{err}”). It's open in JUDO Browser so you can fix it.")
        self.state = "submitted"
        self.tracker.upsert(self.job, "SUBMITTED", verification_evidence="SUBMISSION UNVERIFIED — no confirmation shown",
                            notes=f"after submit: {after.get('title', '')} {url}")
        return self._say("SUBMISSION UNVERIFIED: I pressed submit, but the page shows no confirmation. "
                         "Please check it in JUDO Browser or watch your email for a confirmation.")

    # ── helpers ───────────────────────────────────────────────────────────
    def _resume_name(self) -> str:
        from pathlib import Path
        return Path(self.resume_file).name if self.resume_file else "none"

    def _say(self, text: str) -> str:
        self.message = text
        return text


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")[:60]


def _pick_option(value: str, options: list[str]) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    for o in options:
        if o.strip().lower() == v:
            return o
    for o in options:
        if o.strip().lower().startswith(v) or v.startswith(o.strip().lower()) and len(o) > 2:
            return o
    yes, no = v in ("yes", "y", "true"), v in ("no", "n", "false")
    for o in options:
        ol = o.lower()
        if yes and ol.startswith("yes") or no and ol.startswith("no"):
            return o
    return ""


def _mask(it: Item) -> str:
    if it.source == FILE:
        return f"{it.value.split(chr(92))[-1].split('/')[-1]} (attached)" if it.state == "attached" else "not attached"
    return it.value[:80]
