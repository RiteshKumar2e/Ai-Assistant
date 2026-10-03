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
MONTHS = re.compile(rf"(?i)\b{NUM}\s*(?:-|–|—|to)\s*{NUM}\s*months?\b|\b{NUM}\s*\+?\s*months?\b(?=[^.\n]{{0,40}}\bexperience)")
# a year count that is about something other than the candidate's experience
NOT_EXPERIENCE = re.compile(r"(?i)\b(?:founded|established|in business|since \d{4}|old|anniversary|history|legacy|journey|"
                            r"track record|we have been|over the (?:last|past)|decades?|serving|trusted by|clients|customers|"
                            r"roadmap|vesting|contract (?:term|duration)|warranty|leave|parental|sabbatical)\b")
DEGREE_YEARS = re.compile(r"(?i)^[\s-]*(?:of\s+)?(?:bachelor|degree|college|university|undergrad|program|course|b\.?\s?tech|b\.?e\b|full[- ]time degree)")
FRESHER = re.compile(r"(?i)\b(?:fresher|freshers|new grad(?:uate)?s?|entry[- ]level|recent graduates?|graduating|"
                     r"0\s*(?:-|–|to)\s*\d\s*(?:years?|yrs?)|no (?:prior )?experience (?:required|needed)|campus hire)\b")
SENIOR = re.compile(r"(?i)\b(?:senior|sr\.?|staff|principal|lead|head of|director|architect|manager)\b")
DEGREE_REQ = [
    (re.compile(r"(?i)\bph\.?\s?d\b|doctorate"), 4, "PhD"),
    # "Scrum Master" / "master the stack" are not degrees, and "be" (as in "you will be flexible") is not a B.E.
    (re.compile(r"(?i)\bmaster(?:'|’)?s\b|\bmasters?\s+(?:degree|of|in)\b|\bm\.?\s?tech\b|\bm\.?s\.?\b(?= in\b)|\bmca\b|"
                r"\bmba\b|post[- ]?graduate"), 3, "Master's"),
    (re.compile(r"(?i)\bbachelor(?:'|’)?s?\b|\bb\.?\s?tech\b|(?-i:\bB\.\s?E\b\.?(?=[\s,/.)])|\bBE\b(?=\s*(?:/|,|\(|or\s|in\s+(?:[A-Z][a-z]|CS|IT|EC|EE|CSE))))|\bb\.?sc\b|\bbca\b|"
                r"undergraduate degree|\bdegree in\b|graduate degree|\bgraduate\b(?= in)"), 2, "Bachelor's"),
    (re.compile(r"(?i)\bdiploma\b"), 1, "Diploma"),
]
EQUIVALENT = re.compile(r"(?i)\bor equivalent\b|equivalent (?:practical |work )?experience|or related experience|or similar")
AUTH = re.compile(r"(?i)(?:authori[sz]ed to work|work authori[sz]ation|right to work|work permit|visa sponsorship|"
                  r"sponsorship (?:is )?(?:not )?(?:available|provided)|(?:us|u\.s\.|uk|eu) citizens?(?:hip)?|security clearance|"
                  r"green card|must be (?:a )?(?:citizen|resident)|will not sponsor|unable to sponsor|no sponsorship)[^.\n]{0,80}")
REMOTE = re.compile(r"(?i)\b(?:fully remote|100% remote|remote[- ]first|work from home|wfh|remote)\b")
HYBRID = re.compile(r"(?i)\bhybrid\b")
ONSITE = re.compile(r"(?i)\b(?:on[- ]?site|in[- ]office|work from office|wfo)\b")


# ── places ────────────────────────────────────────────────────────────────
# Indian city names have several spellings in job posts; a few regions group cities.
CITY_ALIASES = {
    "bengaluru": "bengaluru", "bangalore": "bengaluru", "blr": "bengaluru", "gurugram": "gurugram", "gurgaon": "gurugram",
    "mumbai": "mumbai", "bombay": "mumbai", "navi mumbai": "mumbai", "thane": "mumbai", "chennai": "chennai",
    "madras": "chennai", "kolkata": "kolkata", "calcutta": "kolkata", "pune": "pune", "poona": "pune",
    "hyderabad": "hyderabad", "secunderabad": "hyderabad", "noida": "noida", "greater noida": "noida",
    "new delhi": "delhi", "delhi": "delhi", "ghaziabad": "ghaziabad", "faridabad": "faridabad",
    "thiruvananthapuram": "thiruvananthapuram", "trivandrum": "thiruvananthapuram", "kochi": "kochi", "cochin": "kochi",
    "mysuru": "mysuru", "mysore": "mysuru", "ahmedabad": "ahmedabad", "vadodara": "vadodara", "baroda": "vadodara",
    "jaipur": "jaipur", "indore": "indore", "chandigarh": "chandigarh", "mohali": "chandigarh", "coimbatore": "coimbatore",
    "visakhapatnam": "visakhapatnam", "vizag": "visakhapatnam", "bhubaneswar": "bhubaneswar", "jamshedpur": "jamshedpur",
    "ranchi": "ranchi", "nagpur": "nagpur", "lucknow": "lucknow", "kanpur": "kanpur", "patna": "patna", "goa": "goa",
}
REGIONS = {"delhi ncr": {"delhi", "noida", "gurugram", "ghaziabad", "faridabad"},
           "ncr": {"delhi", "noida", "gurugram", "ghaziabad", "faridabad"},
           "karnataka": {"bengaluru", "mysuru"}, "maharashtra": {"mumbai", "pune", "nagpur"},
           "tamil nadu": {"chennai", "coimbatore"}, "telangana": {"hyderabad"}, "haryana": {"gurugram", "faridabad"},
           "uttar pradesh": {"noida", "ghaziabad", "lucknow", "kanpur"}, "kerala": {"kochi", "thiruvananthapuram"},
           "west bengal": {"kolkata"}, "gujarat": {"ahmedabad", "vadodara"}, "jharkhand": {"jamshedpur", "ranchi"}}
COUNTRIES = {"india": "india", "ind": "india", "united states": "united states", "usa": "united states",
             "us": "united states", "u.s.": "united states", "america": "united states", "united kingdom": "united kingdom",
             "uk": "united kingdom", "england": "united kingdom", "canada": "canada", "germany": "germany",
             "singapore": "singapore", "ireland": "ireland", "portugal": "portugal", "netherlands": "netherlands",
             "france": "france", "spain": "spain", "poland": "poland", "australia": "australia", "japan": "japan",
             "philippines": "philippines", "brazil": "brazil", "mexico": "mexico", "uae": "united arab emirates",
             "united arab emirates": "united arab emirates", "europe": "europe", "emea": "europe", "eu": "europe",
             "latam": "latin america", "apac": "apac", "asia": "apac"}
# US states / Canadian provinces as they appear in "City, CA" / "Remote - NY" style locations
_US = {"al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma",
       "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
       "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc"}
ANYWHERE = re.compile(r"(?i)\b(?:anywhere|worldwide|global(?:ly)?|work from anywhere|any location)\b")


def places(text: str) -> dict:
    """What a location string names: {'cities': {...}, 'regions': {...}, 'countries': {...}, 'remote': bool, 'anywhere': bool}.
    'Bangalore' and 'Bengaluru' are the same city; any known Indian city implies India; 'Gurgaon' is in Delhi NCR."""
    low = " " + re.sub(r"[^a-z.]+", " ", (text or "").lower()) + " "
    cities = {canon for name, canon in CITY_ALIASES.items() if f" {name} " in low}
    regions = {r for r, members in REGIONS.items() if f" {r} " in low or cities & members}
    countries = set()
    for name, canon in COUNTRIES.items():
        if len(name) <= 3:          # short codes only as a separate, upper-case token ("Chennai, IN", "Remote - US")
            if re.search(rf"(?<![A-Za-z]){re.escape(name.upper())}(?![A-Za-z])", text or ""):
                countries.add(canon)
        elif f" {name} " in low:
            countries.add(canon)
    if cities:
        countries.add("india")
    if any(re.search(rf"(?<![A-Za-z]){s.upper()}(?![A-Za-z])", text or "") for s in _US) and re.search(r",\s*[A-Z]{2}\b", text or "") \
            and "india" not in countries:
        countries.add("united states")
    return {"cities": cities, "regions": regions, "countries": countries,
            "remote": bool(re.search(r"(?i)\b(?:remote|work from home|wfh|telecommute|distributed)\b", text or "")),
            "anywhere": bool(ANYWHERE.search(text or ""))}


def place_matches(job_location: str, wanted: str) -> bool:
    """Is the job's location inside one place the user asked for ('India', 'Bengaluru', 'Delhi NCR', 'Remote')?"""
    w, j = places(wanted), places(job_location)
    if w["remote"] or w["anywhere"]:
        return j["remote"] or j["anywhere"]
    low = " " + re.sub(r"[^a-z]+", " ", wanted.lower()) + " "
    named = {r for r in REGIONS if f" {r} " in low}      # "Delhi NCR" is the region, not the city of Delhi
    if named:
        return bool(named & j["regions"])
    if w["cities"]:
        return bool(w["cities"] & j["cities"])
    if w["countries"]:
        return bool(w["countries"] & j["countries"])
    return bool(wanted.strip()) and wanted.strip().lower() in (job_location or "").lower()


# sentence breaks — but not inside "B.E. in CS", "e.g. Python", "i.e. SQL"
SENTENCE = re.compile(r"(?<=[.;])(?<!\.[A-Za-z]\.)\s+|\n")


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
    t = clean(text)
    pref = _segments(t)[1]                              # lines under "Nice to have" / "Preferred" headings
    for sentence in re.split(SENTENCE, t):
        if not re.search(r"(?i)year|yr|month", sentence):
            continue
        if NOT_EXPERIENCE.search(sentence) and not re.search(r"(?i)experience|\bexp\b|hands[- ]on|professional", sentence):
            continue                                    # "founded 10 years ago", "20+ years of history", "2 years of parental leave"
        found = None
        for pat in EXP:
            for m in pat.finditer(sentence):
                if DEGREE_YEARS.match(sentence[m.end():]):   # "a 4 year degree", "3 years of college"
                    continue
                found = m
                break
            if found:
                break
        if found:
            nums = [float(x) for x in found.groups() if x]
            lo, hi = nums[0], (nums[1] if len(nums) > 1 else None)
        else:
            mm = MONTHS.search(sentence)                # "6 months to 1 year" is caught above; "0-6 months" here
            if not mm or re.search(r"(?i)\b(?:internship|stipend|probation|duration|contract)\b", sentence):
                continue
            nums = [float(x) for x in mm.groups() if x]
            lo, hi, found = nums[0] / 12, (nums[1] / 12 if len(nums) > 1 else None), mm
        if lo > 25 or (hi is not None and hi < lo):
            continue
        req = not PREFERRED_CUE.search(sentence) and not _in_lines(sentence, pref)
        cand = {"min": lo, "max": hi, "required": req, "text": _window(sentence, found)}
        if best is None or (req and not best["required"]) or (req == best["required"] and lo > best["min"]):
            best = cand
    if best is None and FRESHER.search(clean(text)):
        return {"min": 0.0, "max": 2.0, "required": False, "text": FRESHER.search(clean(text)).group(0), "fresher": True}
    return best


def _in_lines(sentence: str, lines: list[str]) -> bool:
    s = sentence.strip(" •*-–—:\t")
    return bool(s) and any(s in line for line in lines)


def _window(sentence: str, m: re.Match, width: int = 200) -> str:
    """The part of a (possibly run-on) sentence around what was matched — the evidence shown to the user."""
    s = sentence.strip()
    if len(s) <= width:
        return s
    off = len(sentence) - len(sentence.lstrip())
    start = max(0, m.start() - off - width // 3)
    if start:                                           # don't cut a word in half
        sp = s.find(" ", start)
        start = sp + 1 if 0 <= sp < m.start() - off else start
    return ("…" if start else "") + s[start:start + width].strip() + "…"


def education_requirement(text: str) -> dict | None:
    t = clean(text)
    pref = _segments(t)[1]
    found = []
    for sentence in re.split(SENTENCE, t):
        if not re.search(r"(?i)degree|bachelor|master|ph\.?d|b\.?tech|(?-i:B\.?E)\b|m\.?tech|graduate|diploma|education|"
                         r"\bmca\b|\bbca\b|\bmba\b", sentence):
            continue
        # "Bachelor's or Master's", "BE/BTech/MCA": alternatives — the lowest one listed is enough
        hits = [(level, label, m) for pat, level, label in DEGREE_REQ if (m := pat.search(sentence))]
        if hits:
            level, label, m = min(hits, key=lambda h: h[0])
            found.append({"level": level, "label": label,
                          "required": not PREFERRED_CUE.search(sentence) and not _in_lines(sentence, pref),
                          "equivalent_ok": bool(EQUIVALENT.search(sentence)), "text": _window(sentence, m)})
    # "Master's preferred. Bachelor's required." → the requirement is the Bachelor's
    return next((f for f in found if f["required"]), found[0] if found else None)


def analyse(description: str, title: str = "") -> dict:
    """Everything the matcher needs from one job description."""
    text = clean(description)
    req_lines, pref_lines, other = _segments(text)
    if req_lines or pref_lines:
        required = sk.find_skills("\n".join(req_lines))
        preferred = [s for s in sk.find_skills("\n".join(pref_lines)) if s not in required]
    else:  # no headings: sort sentence by sentence
        required, preferred = [], []
        for sentence in re.split(SENTENCE, text):
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
