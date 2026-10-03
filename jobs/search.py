"""
jobs/search.py — find live jobs for the user's roles and places, then read each
actual job page before it is shown.

SEARCH   web search (actions/web_search, the same DDG backend the Research Agent
         uses), one query per role × place × source, newest first when "fresh" is
         asked for. Official career pages and ATS boards (Greenhouse, Lever, Ashby,
         Workday) are tried before aggregators (LinkedIn, Wellfound, Indeed, Naukri,
         Instahyre) — no single site is relied on.

VERIFY   every result is opened: Greenhouse and Lever through their public job
         APIs, anything else through the page's schema.org JobPosting data, falling
         back to the visible text. A job is kept with what the page itself says —
         title, company, location, posting date, closing date, description — and the
         time it was checked. Closed postings are marked expired. A page that can't
         be read stays "unverified"; it is never presented as a checked job.

FRESH    "posted" comes only from the posting (datePosted, the API's created date,
         or "Posted 3 days ago" on the page). Unknown stays unknown: such a job is
         never called fresh.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import parse_qs, urlparse, urlunparse

from jobs import jd as jdmod

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# (name, priority (lower = preferred), search site filter, URL pattern of ONE job posting)
# Checked against live DDG results (Oct 2026): `site:` with a path and `site:a OR site:b` both work; a bare
# `site:jobs.lever.co` / `site:job-boards.greenhouse.io` without the quoted role mostly returns board home pages.
SOURCES = [
    ("Greenhouse", 1, "site:boards.greenhouse.io OR site:job-boards.greenhouse.io", r"greenhouse\.io/[^/?#]+/jobs/\d+"),
    ("Lever", 1, "site:jobs.lever.co", r"jobs\.(?:eu\.)?lever\.co/[^/?#]+/[0-9a-f-]{36}"),
    ("Ashby", 1, "site:jobs.ashbyhq.com", r"jobs\.ashbyhq\.com/[^/?#]+/[0-9a-f-]{36}"),
    ("Workday", 1, "site:myworkdayjobs.com", r"myworkday(?:jobs|site)\.com/.+/job/"),
    ("LinkedIn", 2, "site:linkedin.com/jobs/view", r"linkedin\.com/jobs/view/(?:[^/?#]*-)?\d{6,}"),
    ("Wellfound", 2, "site:wellfound.com/jobs", r"wellfound\.com/jobs/\d+"),
    ("Indeed", 3, "site:indeed.com/viewjob OR site:in.indeed.com/viewjob", r"indeed\.com/(?:viewjob|rc/clk|m/basecamp/viewjob)\b.*[?&]jk="),
    ("Naukri", 3, "site:naukri.com/job-listings", r"naukri\.com/job-listings-"),
    ("Instahyre", 3, "site:instahyre.com/job", r"instahyre\.com/job-\d+"),
    ("Company careers", 1, "careers apply", r"/(?:careers?|jobs?)/(?:[^/?#]+/)*[^/?#]*(?:\d{3,}|[a-z]+-[a-z]+-[a-z]+)"),
]
ATS = ("Greenhouse", "Lever", "Ashby", "Workday")
JOB_SITES = ("LinkedIn", "Wellfound", "Indeed", "Naukri", "Instahyre")
AGGREGATORS = ("Wellfound", "Indeed", "Naukri", "Instahyre")
# Job boards / aggregators / blogs whose /jobs/... pages are search listings, not one company's posting.
NOT_CAREERS = re.compile(r"(?i)(?:^|\.)(?:linkedin|glassdoor|indeed|naukri|foundit|monsterindia|monster|shine|timesjobs|wellfound|"
                         r"angel|instahyre|ziprecruiter|simplyhired|nextraise|career\.now|cutshort|internshala|apna|hirist|"
                         r"iimjobs|techgig|freshersworld|jooble|talent|builtin|careerbuilder|dice|levels\.fyi|ambitionbox|"
                         r"weekday|remoteok|remotive|workingnomads|jobgether|arc\.dev|turing|bing|youtube|medium|"
                         r"facebook|twitter|reddit|quora)\.[a-z.]+$")
LISTING_SLUG = re.compile(r"(?i)/[^/]*(?:-jobs(?:-in-[^/]*)?|jobs-in-[^/]*|-vacancies)/?(?:$|[?#])|/(?:search|results)\b|[?&](?:q|keywords?)=")
EXPIRED = re.compile(r"(?i)(?:no longer (?:accepting applications|available|open)|this (?:job|position|posting|role) (?:has|is) "
                     r"(?:been )?(?:closed|filled|expired|no longer)|job (?:not found|has expired|is closed)|position (?:has been )?filled|"
                     r"applications? (?:are |is )?(?:now )?closed|the job you are looking for)")
AGO = re.compile(r"(?i)\b(?:posted|reposted|active)?\s*(\d{1,2}|an?|one)\s*(minute|hour|day|week|month)s?\s+ago\b")
TODAY = re.compile(r"(?i)\b(?:posted|active)\s+(?:today|just now)|\bjust posted\b")


def normalize_url(url: str) -> str:
    """Same posting, same key: no tracking parameters, no fragment, no trailing slash."""
    p = urlparse((url or "").strip())
    keep = {k: v for k, v in parse_qs(p.query).items() if k.lower() in ("jk", "gh_jid", "jobid", "job_id", "id", "currentjobid")}
    query = "&".join(f"{k}={v[0]}" for k, v in sorted(keep.items()))
    host = p.netloc.lower().removeprefix("www.").replace("job-boards.greenhouse.io", "boards.greenhouse.io")
    path = re.sub(r"/(?:apply|application)$", "", p.path.rstrip("/"))   # the apply page is the same posting
    return urlunparse(("https", host, path, "", query, ""))


def job_key(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:14]


def external_id(url: str) -> str:
    u = normalize_url(url)
    for pat in (r"greenhouse\.io/[^/]+/jobs/(\d+)", r"lever\.co/[^/]+/([0-9a-f-]{36})", r"ashbyhq\.com/[^/]+/([0-9a-f-]{36})",
                r"linkedin\.com/jobs/view/(?:[^/]*-)?(\d+)", r"wellfound\.com/jobs/(\d+)", r"[?&]jk=([0-9a-f]+)",
                r"job-listings-.*-(\d{6,})", r"instahyre\.com/job-(\d+)", r"/job/.*?_(R?\d{4,})", r"(?:gh_jid|jobid|job_id)=(\w+)"):
        m = re.search(pat, u, re.I)
        if m:
            return m.group(1)
    return ""


def source_of(url: str) -> tuple[str, int]:
    for name, prio, _, pat in SOURCES:
        if re.search(pat, url or "", re.I):
            if name == "Company careers":
                host = urlparse(url).netloc.lower().split(":")[0]
                if NOT_CAREERS.search(host) or LISTING_SLUG.search(url):
                    continue
            return name, prio
    return "", 9


# ── fetching ───────────────────────────────────────────────────────────────

def http_fetch(url: str) -> tuple[int, str]:
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=15)
        if "charset" not in r.headers.get("content-type", "").lower():
            r.encoding = "utf-8"        # requests assumes Latin-1 here, which turns "Intel’s" into "Intelâ€™s"
        return r.status_code, r.text
    except Exception:
        return 0, ""


def ddg(query: str, max_results: int, timelimit: str | None) -> list[dict] | None:
    """Results, [] when the search found nothing, None when the search engine refused twice
    (rate limit / every backend failed) — the caller stops sending more queries then."""
    from actions.web_search import _ddg_search
    for attempt in range(2):
        try:
            return _ddg_search(query, max_results=max_results, timelimit=timelimit)
        except Exception as e:
            if "no results" in str(e).lower():
                return []
            if attempt == 0:
                time.sleep(2.5)
    return None


# ── reading one posting ────────────────────────────────────────────────────

def _iso(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            v = float(value)
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).isoformat()
        s = str(value).strip().replace("Z", "+00:00")
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)          # Python 3.10 can't parse 7-digit fractions (some ATS send them)
        dt = datetime.fromisoformat(s if "T" in s or len(s) > 10 else s[:10])
        # always UTC: posted_at strings are compared and sorted as text, so offsets must not differ
        return (dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()
    except ValueError:
        return None


def _posted_from_text(text: str, now: datetime) -> str | None:
    if TODAY.search(text or ""):
        return now.isoformat()
    m = AGO.search(text or "")
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an", "one") else int(m.group(1))
    unit = m.group(2).lower()
    delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n), "day": timedelta(days=n),
             "week": timedelta(weeks=n), "month": timedelta(days=30 * n)}[unit]
    return (now - delta).isoformat()


def _jsonld_job(html_text: str) -> dict | None:
    for blob in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html_text or ""):
        try:
            data = json.loads(blob.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop(0)
            if isinstance(d, dict):
                kind = d.get("@type", "")
                if "jobposting" in [str(k).lower() for k in (kind if isinstance(kind, list) else [kind])]:
                    if str(d.get("title") or "").strip():   # removed Workday jobs keep an empty JobPosting shell
                        return d
                    continue
                stack += d.get("@graph", []) if isinstance(d.get("@graph"), list) else []
    return None


COUNTRY_CODES = {"IN": "India", "US": "United States", "USA": "United States", "GB": "United Kingdom", "UK": "United Kingdom",
                 "CA": "Canada", "DE": "Germany", "SG": "Singapore", "AE": "United Arab Emirates", "AU": "Australia",
                 "IE": "Ireland", "NL": "Netherlands", "FR": "France", "PL": "Poland", "JP": "Japan", "PH": "Philippines"}


def _place(loc) -> str:
    items = loc if isinstance(loc, list) else [loc]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        a = item.get("address") or item.get("postalAddress") or {}
        if isinstance(a, dict):
            country = a.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            country = COUNTRY_CODES.get(str(country).upper(), country) if country else country   # LinkedIn says "IN"
            parts = [a.get("addressLocality"), a.get("addressRegion"), country]
            out.append(", ".join(dict.fromkeys(str(p) for p in parts if p)))
    return " / ".join(x for x in out if x)


def _visible_text(html_text: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text or "", "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for t in soup(["script", "style", "nav", "footer", "noscript", "svg", "header"]):
        t.decompose()
    return title, re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))


def read_posting(url: str, fetch: Callable[[str], tuple[int, str]], now: datetime | None = None) -> dict:
    """Open the posting and return what it says. Never raises."""
    now = now or datetime.now(timezone.utc)
    name, prio = source_of(url)
    job = {"id": job_key(url), "url": url, "apply_url": url, "job_id": external_id(url), "source": name or urlparse(url).netloc,
           "source_priority": prio, "title": "", "company": "", "location": "", "work_mode": "", "employment_type": "",
           "posted_at": None, "posted_from": "", "valid_through": None, "description": "", "status": "unknown",
           "verified": False, "last_verified": now.isoformat(), "error": ""}
    readers = {"Greenhouse": _greenhouse, "Lever": _lever, "Ashby": _ashby, "Workday": _workday}
    try:
        if name in readers and readers[name](job, url, fetch):
            pass
        else:
            _page(job, url, fetch, now)
            if name == "LinkedIn" and not job["posted_from"].startswith("schema.org"):
                _linkedin_guest(job, url, fetch, now)       # auth-wall page: read the public job card instead
    except Exception as e:                              # a bad page must not stop the search
        job["error"] = f"could not read the page: {e}"
    job["description"] = jdmod.clean(job["description"])[:20000]
    if job["valid_through"] and job["valid_through"] < now.isoformat():
        job["status"] = "expired"
    if job["status"] != "expired" and EXPIRED.search(job["description"][:3000] if job["description"] else ""):
        job["status"] = "expired"
    if job["status"] == "unknown" and job["verified"]:
        job["status"] = "active"
    return job


def _greenhouse(job: dict, url: str, fetch) -> bool:
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if not m:
        return False
    code, body = fetch(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
    if code in (404, 410):
        job.update(status="expired", verified=True)
        return True
    if code != 200:
        return False
    d = json.loads(body)
    job.update(title=d.get("title", ""), company=d.get("company_name") or m.group(1).replace("-", " ").title(),
               location=(d.get("location") or {}).get("name", ""), description=d.get("content", ""),
               posted_at=_iso(d.get("first_published")), posted_from="Greenhouse first_published" if d.get("first_published") else "",
               valid_through=_iso(d.get("application_deadline")),
               apply_url=d.get("absolute_url") or url, verified=True)
    return True


def _lever(job: dict, url: str, fetch) -> bool:
    m = re.search(r"jobs\.(eu\.)?lever\.co/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        return False
    eu, org, jid = m.groups()
    code, body = fetch(f"https://api.{eu or ''}lever.co/v0/postings/{org}/{jid}")
    if code in (404, 410):
        job.update(status="expired", verified=True)
        return True
    if code != 200:
        return False
    d = json.loads(body)
    cat = d.get("categories") or {}
    lists = "\n".join(f"{x.get('text', '')}\n{x.get('content', '')}" for x in d.get("lists") or [])
    places = [p for p in cat.get("allLocations") or [] if p and p != cat.get("location")]
    job.update(title=d.get("text", ""), company=org.replace("-", " ").title(),
               location=" / ".join([cat.get("location") or ""] + places).strip(" /"),
               employment_type=cat.get("commitment", ""),
               work_mode="" if (d.get("workplaceType") or "") == "unspecified" else (d.get("workplaceType") or ""),
               description=(d.get("descriptionPlain") or d.get("description") or "") + "\n" + lists + "\n" + (d.get("additionalPlain") or ""),
               posted_at=_iso(d.get("createdAt")), posted_from="Lever createdAt" if d.get("createdAt") else "",
               apply_url=d.get("applyUrl") or url, verified=True)
    return True


def _ashby(job: dict, url: str, fetch) -> bool:
    """Ashby's public job-board API lists every open posting with its publishedAt date. A posting missing
    from the list is closed if its page has no job data either (Ashby then serves an empty 'Jobs' shell)."""
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url)
    if not m:
        return False
    org, jid = m.groups()
    code, body = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
    if code not in (200, 404):
        return False
    d = next((j for j in json.loads(body).get("jobs") or [] if j.get("id") == jid), None) if code == 200 else None
    if d is None:                                       # not on the board (or the board itself is gone)
        pcode, page = fetch(url)
        if pcode in (404, 410) or (pcode == 200 and not _jsonld_job(page)):
            job.update(status="expired", verified=True, error="no longer on the company's Ashby job board")
            return True
        return False                                    # unlisted but still live → read the page
    addr = ((d.get("address") or {}).get("postalAddress") or {})
    loc = d.get("location") or ""
    extra = [x for x in (addr.get("addressRegion"), addr.get("addressCountry")) if x and x.lower() not in loc.lower()]
    places = [", ".join([loc] + extra)] + [s.get("location", "") for s in d.get("secondaryLocations") or [] if isinstance(s, dict)]
    mode = (d.get("workplaceType") or "").lower()
    comp = ((d.get("compensation") or {}).get("compensationTierSummary") or "")
    job.update(title=d.get("title", ""), company=org.replace("-", " ").title(), location=" / ".join(p for p in places if p),
               work_mode=mode if mode in ("remote", "hybrid", "onsite") else ("remote" if d.get("isRemote") else ""),
               employment_type=d.get("employmentType", ""),
               description=(d.get("descriptionHtml") or d.get("descriptionPlain") or "") + (f"\nCompensation: {comp}" if comp else ""),
               posted_at=_iso(d.get("publishedAt")), posted_from="Ashby publishedAt" if d.get("publishedAt") else "",
               apply_url=d.get("applyUrl") or url, verified=True)
    return True


def _workday_api(url: str) -> str:
    """https://acme.wd5.myworkdayjobs.com/en-US/Site/job/City/Title_R123/apply → its public 'cxs' JSON URL."""
    p = urlparse(url)
    parts = [x for x in re.sub(r"/apply(?:/.*)?$", "", p.path).split("/") if x]
    if parts and re.fullmatch(r"[a-z]{2}(?:-[A-Za-z]{2})?", parts[0]):
        parts = parts[1:]
    if "myworkdaysite.com" in p.netloc and parts[:1] == ["recruiting"] and len(parts) > 3:
        tenant, site, rest = parts[1], parts[2], parts[3:]
    elif len(parts) > 1:
        tenant, site, rest = p.netloc.split(".")[0], parts[0], parts[1:]
    else:
        return ""
    return f"https://{p.netloc}/wday/cxs/{tenant}/{site}/{'/'.join(rest)}" if rest[:1] == ["job"] else ""


def _workday(job: dict, url: str, fetch) -> bool:
    """Workday pages are a JavaScript shell; the same data comes from the site's public cxs JSON
    (startDate = the day it was posted). A removed posting answers 403 'S22' / 404 there and its page
    then has no JobPosting data."""
    api = _workday_api(url)
    if not api:
        return False
    code, body = fetch(api)
    if code in (403, 404, 410):
        pcode, page = fetch(url)
        if pcode in (404, 410) or (pcode == 200 and not _jsonld_job(page)):
            job.update(status="expired", verified=True, error="Workday no longer shows this posting")
            return True
        return False
    if code != 200:
        return False
    d = json.loads(body)
    info = d.get("jobPostingInfo") or {}
    if not info.get("title"):
        return False
    org = (d.get("hiringOrganization") or {}).get("name") or urlparse(url).netloc.split(".")[0].title()
    places = [info.get("location") or ""] + [x for x in info.get("additionalLocations") or [] if isinstance(x, str)]
    remote = (info.get("remoteType") or "").lower()
    job.update(title=info["title"], company=org, location=" / ".join(p for p in places if p),
               work_mode="remote" if "remote" in remote else "hybrid" if "hybrid" in remote else "onsite" if "site" in remote else "",
               employment_type=info.get("timeType", ""), description=info.get("jobDescription", ""),
               posted_at=_iso(info.get("startDate")), posted_from="Workday startDate" if info.get("startDate") else "",
               apply_url=info.get("externalUrl") or url, verified=True)
    if info.get("canApply") is False or info.get("posted") is False:
        job["status"] = "expired"
    return True


def _linkedin_guest(job: dict, url: str, fetch, now: datetime) -> None:
    """LinkedIn's public job card (what logged-out visitors see). Only this job's own top card is read —
    the full page also lists 'similar jobs' whose '3 days ago' must not be taken for this one's."""
    jid = external_id(url)
    if not jid.isdigit():
        return
    code, body = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}")
    if code != 200 or "top-card" not in (body or ""):
        return
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    pick = lambda sel: (soup.select_one(sel).get_text(" ", strip=True) if soup.select_one(sel) else "")
    desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    criteria = {pick_h.get_text(strip=True).lower(): pick_h.find_next(class_="description__job-criteria-text").get_text(strip=True)
                for pick_h in soup.select(".description__job-criteria-subheader")
                if pick_h.find_next(class_="description__job-criteria-text")}
    ago = pick(".posted-time-ago__text")
    job.update(title=pick(".top-card-layout__title") or job["title"], company=pick(".topcard__org-name-link") or job["company"],
               location=pick(".topcard__flavor--bullet") or job["location"],
               description=desc.decode_contents() if desc else job["description"],
               employment_type=criteria.get("employment type", job["employment_type"]),
               seniority=criteria.get("seniority level", ""), verified=bool(desc) or job["verified"], error="")
    when = _posted_from_text(ago + " ago" if ago and "ago" not in ago else ago, now) if ago else None
    job.update(posted_at=when, posted_from=f"LinkedIn (“{ago}”)" if when else "")
    job["status"] = "expired" if soup.select_one(".closed-job") else "unknown"


def _page(job: dict, url: str, fetch, now: datetime) -> None:
    code, body = fetch(url)
    if code in (404, 410):
        job.update(status="expired", verified=True, error=f"HTTP {code}")
        return
    if code != 200 or not body:
        job["error"] = f"the site didn't let me read the page (HTTP {code or 'no answer'})"
        return
    data = _jsonld_job(body)
    page_title, text = _visible_text(body)
    if data:
        org = data.get("hiringOrganization") or {}
        remote = str(data.get("jobLocationType", "")).upper() == "TELECOMMUTE"
        job.update(title=data.get("title", "") or page_title, company=org.get("name", "") if isinstance(org, dict) else str(org),
                   location=("Remote" + (f" - {_place(data.get('applicantLocationRequirements'))}" if data.get("applicantLocationRequirements") else ""))
                   if remote else _place(data.get("jobLocation")),
                   work_mode="remote" if remote else "", employment_type=str(data.get("employmentType", "")),
                   description=data.get("description", "") or text, posted_at=_iso(data.get("datePosted")),
                   posted_from="schema.org datePosted" if data.get("datePosted") else "",
                   valid_through=_iso(data.get("validThrough")), verified=True)
        bs = data.get("baseSalary") or {}
        val = bs.get("value") if isinstance(bs, dict) else None
        if isinstance(val, dict) and val.get("maxValue"):
            job["salary_max"] = val.get("maxValue")
            job["salary_currency"] = bs.get("currency", "")
        if not job["company"] and job["source"] == "Workday":
            job["company"] = urlparse(url).netloc.split(".")[0].title()
    else:
        # No structured job data. On a job site the visible text is the posting; on any other site it may
        # just as well be a listing or a blog post, so it isn't presented as a checked job.
        job.update(title=page_title.split("|")[0].split(" - ")[0].strip(), description=text,
                   verified=len(text) > 400 and job["source"] in JOB_SITES)
        if not job["verified"]:
            job["error"] = "the page has no job-posting data, so I couldn't confirm it is one open job"
    if not job["posted_at"] and job["source"] in JOB_SITES:   # "3 days ago" on a blog or listing means nothing
        guess = _posted_from_text(text[:5000], now)
        if guess:
            job.update(posted_at=guess, posted_from="the page (“… ago”)")
    if EXPIRED.search(text[:4000]):
        job["status"] = "expired"


# ── the search ─────────────────────────────────────────────────────────────

REMOTE_PLACE = re.compile(r"(?i)^\s*(?:remote|anywhere|work from home|wfh)\s*$")
SENIOR_HINT = re.compile(r"(?i)\b(?:senior|sr\.?|staff|principal|lead|head|director|manager|architect|vp|iii|iv)\b")


def build_queries(prefs: dict, fresh: bool, max_queries: int = 30) -> list[tuple[str, str]]:
    """(query, source name) pairs, most useful first, at most `max_queries` (DDG rate-limits bursts).

    First place: every role on every ATS board and LinkedIn, then one company-careers query per role,
    then one query per aggregator. Further places: LinkedIn and Greenhouse per role. "Remote" alone
    finds mostly US-only remote jobs, so it is searched together with the user's other place ("remote India")."""
    roles = list(dict.fromkeys(r.strip() for r in prefs.get("roles") or ["software engineer"] if r and r.strip()))[:4]
    places = [p.strip() for p in prefs.get("locations") or [] if p and p.strip()]
    if str(prefs.get("remote")) == "remote" and not any(REMOTE_PLACE.match(p) for p in places):
        places.append("remote")
    fixed = [p for p in places if not REMOTE_PLACE.match(p)]
    places = list(dict.fromkeys(f"remote {fixed[0]}" if REMOTE_PLACE.match(p) and fixed else p for p in places))[:3] or [""]
    lo, hi = (list(prefs.get("experience_range") or [0, 2]) + [None, None])[:2]
    level = "entry level" if hi is not None and float(hi) <= 2 else ""
    site = {name: s for name, _, s, _ in SOURCES}
    q = lambda role, place, name: (" ".join(x for x in (f'"{role}"', place, level, site[name]) if x), name)
    out = [q(r, places[0], n) for r in roles for n in (*ATS, "LinkedIn")]
    out += [q(r, places[0], "Company careers") for r in roles]
    out += [q(roles[i % len(roles)], places[0], n) for i, n in enumerate(AGGREGATORS)]
    out += [q(r, p, n) for p in places[1:] for r in roles for n in ("LinkedIn", "Greenhouse")]
    return list(dict.fromkeys(out))[:max_queries]


ROLE_GENERIC = {"engineer", "developer", "programmer", "sde", "swe", "junior", "associate", "entry", "level", "fresher", "graduate",
                "intern", "and", "of", "the", "i", "ii"}
ROLE_DOER = re.compile(r"(?i)\b(?:engineer|developer|programmer|sde|swe|scientist|engineering)\b")


def title_fits(title: str, roles: list[str]) -> bool:
    """'Software Development Engineer' fits 'Software Engineer'; 'Back-end Developer' fits 'Backend Developer';
    'ML Engineer' fits 'Machine Learning Engineer'. A 'Sales Engineer' fits none of them."""
    norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^a-z+#. ]", " ", (s or "").lower()
                                                .replace("back-end", "backend").replace("back end", "backend")))
    t = norm(title)
    t = re.sub(r"\bml\b", "machine learning", re.sub(r"\b(?:sde|swe)\b", "software engineer", t))
    t = re.sub(r"\bai\s*/\s*machine learning\b|\bai\b", "ai machine learning", t)
    if not ROLE_DOER.search(t):
        return False
    for role in roles or []:
        words = [w for w in norm(role).split() if w not in ROLE_GENERIC] or norm(role).split()
        if words and all(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
            return True
    return False


# Boards whose public API lists every open job (with its posting date), found from board pages or postings
# in the search results. Search engines index ATS postings long after they close; the board API doesn't.
BOARDS = [
    ("Greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?!embed\b)([A-Za-z0-9_-]+)"),
     "https://boards-api.greenhouse.io/v1/boards/{org}/jobs"),
    ("Lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)"), "https://api.lever.co/v0/postings/{org}?mode=json"),
    ("Ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
     "https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"),
]


def board_of(url: str) -> tuple[str, str] | None:
    for name, pat, _ in BOARDS:
        m = pat.search(url or "")
        if m and m.group(1).lower() not in ("v1", "embed", "api"):
            return name, m.group(1)
    return None


def board_openings(name: str, org: str, fetch) -> list[dict]:
    """[{url, title, location, posted_at}] for every open job on one company's ATS board."""
    api = next(a for n, _, a in BOARDS if n == name).format(org=org)
    code, body = fetch(api)
    if code != 200:
        return []
    try:
        d = json.loads(body)
    except ValueError:
        return []
    out = []
    if name == "Greenhouse":
        for j in d.get("jobs") or []:
            out.append({"url": f"https://job-boards.greenhouse.io/{org}/jobs/{j.get('id')}", "title": j.get("title", ""),
                        "location": (j.get("location") or {}).get("name", ""), "posted_at": _iso(j.get("first_published"))})
    elif name == "Lever":
        for j in d if isinstance(d, list) else []:
            cat = j.get("categories") or {}
            out.append({"url": j.get("hostedUrl", ""), "title": j.get("text", ""),
                        "location": " / ".join([cat.get("location") or ""] + (cat.get("allLocations") or []))
                        + (" remote" if j.get("workplaceType") == "remote" else ""), "posted_at": _iso(j.get("createdAt"))})
    else:
        for j in d.get("jobs") or []:
            if j.get("isListed") is False:
                continue
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            out.append({"url": j.get("jobUrl", ""), "title": j.get("title", ""),
                        "location": " / ".join([j.get("location") or "", addr.get("addressCountry") or ""]
                                               + [s.get("location", "") for s in j.get("secondaryLocations") or []
                                                  if isinstance(s, dict)])
                        + (" remote" if (j.get("workplaceType") or "").lower() == "remote" else ""),
                        "posted_at": _iso(j.get("publishedAt"))})
    return [o for o in out if o["url"]]


def _board_picks(openings: list[dict], prefs: dict, cutoff: str | None, per_board: int) -> list[dict]:
    """The openings on one board that fit the wanted roles and places (newest first)."""
    roles = prefs.get("roles") or []
    wanted = [p for p in prefs.get("locations") or [] if p and p.strip()]
    hi = (list(prefs.get("experience_range") or [0, 99]) + [99])[1]
    junior_only = hi is not None and float(hi) <= 2
    picks = []
    for o in openings:
        if not title_fits(o["title"], roles) or (junior_only and SENIOR_HINT.search(o["title"])):
            continue
        if wanted and not any(jdmod.place_matches(o["location"], w) for w in wanted):
            continue
        if cutoff and o["posted_at"] and o["posted_at"] < cutoff:
            continue
        picks.append(o)
    return sorted(picks, key=lambda o: o["posted_at"] or "", reverse=True)[:per_board]


def _rank(c: dict, prefs: dict) -> tuple:
    """Fetch order for search hits: official sources first, then titles that name a wanted role, and —
    for a 0–2 years search — senior-sounding titles last (they are still read if there's room)."""
    hint = f"{c.get('title', '')} {urlparse(c['url']).path}".replace("-", " ")
    hi = (list(prefs.get("experience_range") or [0, 99]) + [99])[1]
    senior = bool(SENIOR_HINT.search(hint)) and hi is not None and float(hi) <= 2
    words = {w for r in prefs.get("roles") or [] for w in re.findall(r"[a-z]{3,}", r.lower())}
    overlap = sum(1 for w in words if w in hint.lower())
    return senior, not c.get("board"), c["prio"], -overlap


def search_jobs(prefs: dict, *, fresh_days: int | None = None, max_jobs: int = 25, max_pages: int = 45,
                max_queries: int = 30, max_boards: int = 15, searcher: Callable | None = None, fetcher: Callable | None = None,
                now: datetime | None = None, progress: Callable[[str], None] | None = None) -> list[dict]:
    """Live search → verified job records (unverified ones flagged). Newest known postings first."""
    searcher, fetcher = searcher or ddg, fetcher or http_fetch
    now = now or datetime.now(timezone.utc)
    limit = None if not fresh_days else ("d" if fresh_days <= 1 else "w" if fresh_days <= 7 else "m")
    # ATS pages carry their own posting date and the search engine's crawl date says little about it;
    # a too-tight time filter there mostly returns unrelated boards, so they get one step looser.
    ats_limit = {None: None, "d": "w", "w": "m", "m": "m"}[limit]
    urls: dict[str, dict] = {}
    boards: dict[tuple[str, str], None] = {}
    queries = build_queries(prefs, bool(fresh_days), max_queries)
    say = progress or (lambda s: None)
    say(f"Searching {len({n for _, n in queries})} job sources…")
    enough = max_pages * 2

    def run(q):
        query, name = q
        return name, searcher(query, 10, ats_limit if name in ATS else limit)
    with ThreadPoolExecutor(4) as pool:
        for start in range(0, len(queries), 4):
            batch = list(pool.map(run, queries[start:start + 4]))
            for name, rows in batch:
                for r in rows or []:
                    u = r.get("url") or r.get("href") or ""
                    if board_of(u):
                        boards.setdefault(board_of(u), None)
                    src, prio = source_of(u)
                    if not src:
                        continue
                    key = normalize_url(u)
                    if key not in urls:
                        urls[key] = {"url": u, "title": r.get("title", ""), "snippet": r.get("snippet", ""), "prio": prio}
            if all(rows is None for _, rows in batch):
                say("The search engine stopped answering (rate limit) — using what was found so far.")
                break
            if len(urls) >= enough:
                break
    cache: dict[str, tuple[int, str]] = {}

    def fetch_once(u: str) -> tuple[int, str]:   # one Ashby board / page per run, however many postings share it
        if u not in cache:
            cache[u] = fetcher(u)
        return cache[u]

    if boards and max_boards:
        say(f"Checking the open jobs on {min(len(boards), max_boards)} company job boards…")
        cutoff = (now - timedelta(days=fresh_days)).isoformat() if fresh_days else None
        with ThreadPoolExecutor(6) as pool:
            lists = list(pool.map(lambda b: board_openings(b[0], b[1], fetch_once), list(boards)[:max_boards]))
        for (name, org), openings in zip(list(boards)[:max_boards], lists):
            for o in _board_picks(openings, prefs, cutoff, per_board=3):
                key = normalize_url(o["url"])
                src, prio = source_of(o["url"])
                if src and key not in urls:
                    urls[key] = {"url": o["url"], "title": o["title"], "snippet": "", "prio": prio, "board": True}
                elif key in urls:
                    urls[key]["board"] = True
    candidates = sorted(urls.values(), key=lambda c: _rank(c, prefs))[:max_pages]
    say(f"Found {len(urls)} possible postings — checking {len(candidates)} job pages…")
    with ThreadPoolExecutor(8) as pool:
        jobs = list(pool.map(lambda c: read_posting(c["url"], fetch_once, now), candidates))
    for j, c in zip(jobs, candidates):
        if not j["title"] and c.get("title"):         # e.g. a closed posting: say which one it was
            j["title"] = re.split(r"\s+[|–-]\s+|\s+at\s+", c["title"])[0].strip()[:120]
            j["title_from"] = "search result"
    jobs = dedupe(jobs)
    if fresh_days:
        cutoff = (now - timedelta(days=fresh_days)).isoformat()
        jobs = [j for j in jobs if not j["posted_at"] or j["posted_at"] >= cutoff]
    jobs.sort(key=lambda j: (j["status"] == "expired", not j["verified"], j["posted_at"] is None,
                             "" if not j["posted_at"] else "~" + j["posted_at"]), reverse=False)
    jobs.sort(key=lambda j: (j["status"] == "expired", not j["verified"], j["posted_at"] is None))
    # newest first inside each group
    groups: dict[tuple, list] = {}
    for j in jobs:
        groups.setdefault((j["status"] == "expired", not j["verified"], j["posted_at"] is None), []).append(j)
    ordered = []
    for k in sorted(groups):
        ordered += sorted(groups[k], key=lambda j: j["posted_at"] or "", reverse=True)
    return ordered[:max_jobs]


def dedupe(jobs: list[dict]) -> list[dict]:
    """One record per real job: same normalized URL, same ATS job id, or same company + title + place.
    The official/ATS copy wins over an aggregator's copy."""
    best: dict[str, dict] = {}
    alias: dict[str, str] = {}
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    for j in sorted(jobs, key=lambda j: (j.get("source_priority", 9), not j.get("verified"))):
        keys = [normalize_url(j["url"])]
        if j.get("job_id"):
            keys.append(f"{j['source']}:{j['job_id']}")
        if j.get("company") and j.get("title"):
            keys.append(f"{norm(j['company'])}|{norm(j['title'])}|{norm(j.get('location', ''))[:30]}")
        hit = next((alias[k] for k in keys if k in alias), None)
        if hit:
            seen = best[hit]
            seen.setdefault("also_on", []).append({"source": j["source"], "url": j["url"]})
            if not seen.get("posted_at") and j.get("posted_at"):
                seen["posted_at"], seen["posted_from"] = j["posted_at"], j["posted_from"] + f" ({j['source']})"
            continue
        best[keys[0]] = j
        for k in keys:
            alias[k] = keys[0]
    return list(best.values())


def freshness(job: dict, now: datetime | None = None) -> str:
    """'posted today' / 'posted 3 days ago' — or 'posting date unknown'. Never guesses."""
    if not job.get("posted_at"):
        return "posting date unknown"
    now = now or datetime.now(timezone.utc)
    try:
        days = (now - datetime.fromisoformat(job["posted_at"])).days
    except ValueError:
        return "posting date unknown"
    return "posted today" if days <= 0 else "posted yesterday" if days == 1 else f"posted {days} days ago"


def is_fresh(job: dict, days: int = 7, now: datetime | None = None) -> bool:
    if not job.get("posted_at"):
        return False
    now = now or datetime.now(timezone.utc)
    try:
        return now - datetime.fromisoformat(job["posted_at"]) <= timedelta(days=days)
    except ValueError:
        return False
