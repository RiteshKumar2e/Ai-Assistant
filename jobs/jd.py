"""
jobs/jd.py — read a job description the way a recruiter would: what is REQUIRED,
what is merely PREFERRED, how much experience and which degree it asks for,
where the job is, and whether it says anything about work authorisation.

Sections are found by their headings ("Requirements", "Must have", "Nice to
have", "Bonus points"…). Without headings, a sentence counts as preferred when it
says so ("preferred", "nice to have", "a plus", "bonus") and as required otherwise.
"""
from __future__ import annotations

import html
import re

from jobs import skills as sk

REQUIRED_HEAD = re.compile(r"(?i)^\W*(?:requirements?|required(?: skills| qualifications)?|minimum qualifications|basic qualifications|"
                           r"must[- ]haves?|what (?:you(?:'ll)? need|we(?:'re| are) looking for)|who you are|you (?:have|bring|should have)|"
                           r"qualifications|skills (?:required|&? ?experience)|key skills|eligibility|"
                           r"what you bring|your profile|about you|desired profile)\b.{0,40}$")
PREFERRED_HEAD = re.compile(r"(?i)^\W*(?:preferred(?: qualifications| skills)?|nice[- ]to[- ]haves?|good to have|bonus(?: points)?|"
                            r"pluses|additional qualifications|it(?:'s| is| would be) (?:a )?(?:plus|great|nice) if|"
                            r"extra credit|desirable|added advantage)\b.{0,40}$")
OTHER_HEAD = re.compile(r"(?i)^\W*(?:responsibilities|what you(?:'ll| will) do|the role|about (?:the )?(?:role|company|us|team)|"
                        r"benefits|perks|what we offer|why join|compensation|our culture|how to apply|equal opportunity)\b.{0,40}$")
PREFERRED_CUE = re.compile(r"(?i)\b(?:preferred|nice[- ]to[- ]have|good to have|a plus|is a plus|are a plus|plus point|bonus|"
                           r"advantageous|desirable|ideally|added advantage|familiarity with|exposure to)\b")

NUM = r"(\d{1,2}(?:\.\d)?)"
EXP = [
    re.compile(rf"(?i)\b{NUM}\s*(?:\+|plus)?\s*(?:-|–|—|to)\s*{NUM}\s*\+?\s*(?:years?|yrs?)\b"),
    re.compile(rf"(?i)\b(?:minimum|min\.?|at least|atleast)\s*(?:of\s*)?{NUM}\s*\+?\s*(?:years?|yrs?)\b"),
    re.compile(rf"(?i)\b{NUM}\s*\+\s*(?:years?|yrs?)\b"),
    re.compile(rf"(?i)\b{NUM}\s*(?:or more|and above)\s*(?:years?|yrs?)\b"),
    re.compile(rf"(?i)\b{NUM}\s*(?:years?|yrs?)\b(?=[^.\n]{{0,40}}\bexperience)"),
]
FRESHER = re.compile(r"(?i)\b(?:fresher|freshers|new grad(?:uate)?s?|entry[- ]level|recent graduates?|graduating|"
                     r"0\s*(?:-|–|to)\s*\d\s*(?:years?|yrs?)|no (?:prior )?experience (?:required|needed)|campus hire)\b")
SENIOR = re.compile(r"(?i)\b(?:senior|sr\.?|staff|principal|lead|head of|director|architect|manager)\b")
DEGREE_REQ = [
    (re.compile(r"(?i)\bph\.?\s?d\b|doctorate"), 4, "PhD"),
    (re.compile(r"(?i)\bmaster'?s?\b|\bm\.?\s?tech\b|\bm\.?s\.?\b(?= in\b)|\bmca\b|\bmba\b|post[- ]?graduate"), 3, "Master's"),
    (re.compile(r"(?i)\bbachelor'?s?\b|\bb\.?\s?tech\b|\bb\.?\s?e\b(?=[\s,/])|\bb\.?sc\b|\bbca\b|undergraduate degree|"
                r"\bdegree in\b|graduate degree|\bgraduate\b(?= in)"), 2, "Bachelor's"),
    (re.compile(r"(?i)\bdiploma\b"), 1, "Diploma"),
]
EQUIVALENT = re.compile(r"(?i)\bor equivalent\b|equivalent (?:practical |work )?experience|or related experience|or similar")
AUTH = re.compile(r"(?i)(?:authori[sz]ed to work|work authori[sz]ation|right to work|work permit|visa sponsorship|"
                  r"sponsorship (?:is )?(?:not )?(?:available|provided)|(?:us|u\.s\.|uk|eu) citizens?(?:hip)?|security clearance|"
                  r"green card|must be (?:a )?(?:citizen|resident)|will not sponsor|unable to sponsor|no sponsorship)[^.\n]{0,80}")
REMOTE = re.compile(r"(?i)\b(?:fully remote|100% remote|remote[- ]first|work from home|wfh|remote)\b")
HYBRID = re.compile(r"(?i)\bhybrid\b")
ONSITE = re.compile(r"(?i)\b(?:on[- ]?site|in[- ]office|work from office|wfo)\b")


def clean(text: str) -> str:
    """HTML or text → plain text with one item per line."""
    t = html.unescape(text or "")
    if "<" in t and ">" in t:
        t = re.sub(r"(?i)<\s*(?:br|/p|/li|/h\d|/div|/tr|li)[^>]*>", "\n", t)
        t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\r", "").replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def _segments(text: str) -> tuple[list[str], list[str], list[str]]:
    """Lines under required / preferred / other headings. No headings at all → everything 'unsorted'."""
    req, pref, other = [], [], []
    bucket = None
    seen_head = False
    for line in clean(text).split("\n"):
        s = line.strip(" •*-–—:\t")
        if not s:
            continue
        if len(s) < 70 and PREFERRED_HEAD.match(s):
            bucket, seen_head = pref, True
            continue
        if len(s) < 70 and REQUIRED_HEAD.match(s):
            bucket, seen_head = req, True
            continue
        if len(s) < 70 and OTHER_HEAD.match(s):
            bucket, seen_head = other, True
            continue
        (bucket if bucket is not None else other).append(s)
    if not seen_head:
        return [], [], other
    return req, pref, other


def experience_requirement(text: str) -> dict | None:
    """{'min': 2.0, 'max': 4.0 | None, 'required': bool, 'text': '...'} — the strictest stated range."""
    best = None
    for sentence in re.split(r"(?<=[.;\n])\s+|\n", clean(text)):
        if not re.search(r"(?i)year|yr", sentence):
            continue
        if re.search(r"(?i)\b(?:founded|in business|since|old|company|we have been|over the (?:last|past))\b", sentence) \
                and not re.search(r"(?i)experience", sentence):
            continue
        for pat in EXP:
            m = pat.search(sentence)
            if not m:
                continue
            nums = [float(x) for x in m.groups() if x]
            lo, hi = nums[0], (nums[1] if len(nums) > 1 else None)
            if lo > 25 or (hi is not None and hi < lo):
                break
            req = not PREFERRED_CUE.search(sentence)
            cand = {"min": lo, "max": hi, "required": req, "text": sentence.strip()[:200]}
            if best is None or (req and not best["required"]) or (req == best["required"] and lo > best["min"]):
                best = cand
            break
    if best is None and FRESHER.search(clean(text)):
        return {"min": 0.0, "max": 2.0, "required": False, "text": FRESHER.search(clean(text)).group(0), "fresher": True}
    return best


def education_requirement(text: str) -> dict | None:
    t = clean(text)
    for sentence in re.split(r"(?<=[.;\n])\s+|\n", t):
        if not re.search(r"(?i)degree|bachelor|master|ph\.?d|b\.?tech|b\.?e\b|m\.?tech|graduate|diploma|education", sentence):
            continue
        for pat, level, label in DEGREE_REQ:
            if pat.search(sentence):
                return {"level": level, "label": label, "required": not PREFERRED_CUE.search(sentence),
                        "equivalent_ok": bool(EQUIVALENT.search(sentence)), "text": sentence.strip()[:200]}
    return None


def analyse(description: str, title: str = "") -> dict:
    """Everything the matcher needs from one job description."""
    text = clean(description)
    req_lines, pref_lines, other = _segments(text)
    if req_lines or pref_lines:
        required = sk.find_skills("\n".join(req_lines))
        preferred = [s for s in sk.find_skills("\n".join(pref_lines)) if s not in required]
    else:  # no headings: sort sentence by sentence
        required, preferred = [], []
        for sentence in re.split(r"(?<=[.;])\s+|\n", text):
            found = sk.find_skills(sentence)
            if not found:
                continue
            target = preferred if PREFERRED_CUE.search(sentence) else required
            target.extend(s for s in found if s not in required and s not in preferred)
    in_title = sk.find_skills(title)
    required = list(dict.fromkeys(in_title + required))
    preferred = [s for s in preferred if s not in required]
    mentioned = [s for s in sk.find_skills(text) if s not in required and s not in preferred]
    auth = AUTH.search(text)
    mode = ("remote" if REMOTE.search(text) and not ONSITE.search(text) and not HYBRID.search(text)
            else "hybrid" if HYBRID.search(text) else "onsite" if ONSITE.search(text) else "")
    return {"required_skills": required, "preferred_skills": preferred, "mentioned_skills": mentioned,
            "experience": experience_requirement(text), "education": education_requirement(text),
            "work_authorization": auth.group(0).strip() if auth else "",
            "work_mode": mode, "senior_title": bool(SENIOR.search(title or "")),
            "internship": bool(re.search(r"(?i)\bintern(?:ship)?\b", title or ""))}
