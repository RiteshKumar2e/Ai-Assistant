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
SOURCES = [
    ("Greenhouse", 1, "site:boards.greenhouse.io OR site:job-boards.greenhouse.io", r"greenhouse\.io/[^/]+/jobs/\d+"),
    ("Lever", 1, "site:jobs.lever.co", r"jobs\.lever\.co/[^/]+/[0-9a-f-]{36}"),
    ("Ashby", 1, "site:jobs.ashbyhq.com", r"jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{36}"),
    ("Workday", 1, "site:myworkdayjobs.com", r"myworkdayjobs\.com/.+/job/"),
    ("LinkedIn", 2, "site:linkedin.com/jobs/view", r"linkedin\.com/jobs/view/"),
    ("Wellfound", 2, "site:wellfound.com/jobs", r"wellfound\.com/jobs/\d+"),
    ("Indeed", 3, "site:indeed.com/viewjob OR site:in.indeed.com/viewjob", r"indeed\.com/(?:viewjob|rc/clk|m/basecamp/viewjob)"),
    ("Naukri", 3, "site:naukri.com/job-listings", r"naukri\.com/job-listings-"),
    ("Instahyre", 3, "site:instahyre.com/job", r"instahyre\.com/job-\d+"),
    ("Company careers", 1, "careers apply", r"/(?:careers?|jobs?)/(?:[^/?#]+/)*[^/?#]*(?:\d{3,}|[a-z]+-[a-z]+-[a-z]+)"),
]
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
    return urlunparse(("https", host, p.path.rstrip("/"), "", query, ""))


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
            return name, prio
    return "", 9


# ── fetching ───────────────────────────────────────────────────────────────

def http_fetch(url: str) -> tuple[int, str]:
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=15)
        return r.status_code, r.text
    except Exception:
        return 0, ""


def ddg(query: str, max_results: int, timelimit: str | None) -> list[dict]:
    from actions.web_search import _ddg_search
    try:
        return _ddg_search(query, max_results=max_results, timelimit=timelimit)
    except Exception:
        return []


# ── reading one posting ────────────────────────────────────────────────────

def _iso(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            v = float(value)
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).isoformat()
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s if "T" in s or len(s) > 10 else s[:10])
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()
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
                if str(d.get("@type", "")).lower() == "jobposting":
                    return d
                stack += d.get("@graph", []) if isinstance(d.get("@graph"), list) else []
    return None


def _place(loc) -> str:
    items = loc if isinstance(loc, list) else [loc]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        a = item.get("address") or {}
        if isinstance(a, dict):
            country = a.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
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
    try:
        if name == "Greenhouse" and _greenhouse(job, url, fetch):
            pass
        elif name == "Lever" and _lever(job, url, fetch):
            pass
        else:
            _page(job, url, fetch, now)
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
               apply_url=d.get("absolute_url") or url, verified=True)
    return True


def _lever(job: dict, url: str, fetch) -> bool:
    m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        return False
    code, body = fetch(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    if code in (404, 410):
        job.update(status="expired", verified=True)
        return True
    if code != 200:
        return False
    d = json.loads(body)
    cat = d.get("categories") or {}
    lists = "\n".join(f"{x.get('text', '')}\n{x.get('content', '')}" for x in d.get("lists") or [])
    job.update(title=d.get("text", ""), company=m.group(1).replace("-", " ").title(), location=cat.get("location", ""),
               employment_type=cat.get("commitment", ""), work_mode=(d.get("workplaceType") or "").replace("onsite", "onsite"),
               description=(d.get("descriptionPlain") or d.get("description") or "") + "\n" + lists + "\n" + (d.get("additionalPlain") or ""),
               posted_at=_iso(d.get("createdAt")), posted_from="Lever createdAt" if d.get("createdAt") else "",
               apply_url=d.get("applyUrl") or url, verified=True)
    return True


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
    else:
        job.update(title=page_title.split("|")[0].split(" - ")[0].strip(), description=text,
                   verified=len(text) > 400)
    if not job["posted_at"]:
        guess = _posted_from_text(text[:5000], now)
        if guess:
            job.update(posted_at=guess, posted_from="the page (“… ago”)")
    if EXPIRED.search(text[:4000]):
        job["status"] = "expired"


# ── the search ─────────────────────────────────────────────────────────────

def build_queries(prefs: dict, fresh: bool) -> list[tuple[str, str]]:
    """(query, source name) pairs — official/ATS boards first."""
    roles = (prefs.get("roles") or ["software engineer"])[:4]
    places = (prefs.get("locations") or [""])[:3]
    if str(prefs.get("remote")) == "remote" and "remote" not in [p.lower() for p in places]:
        places = ["remote"] + places
    lo, hi = (prefs.get("experience_range") or [0, 2])[:2]
    level = "entry level" if hi <= 2 else ""
    out = []
    for role in roles:
        for place in places:
            for name, prio, site, _ in sorted(SOURCES, key=lambda s: s[1]):
                q = " ".join(x for x in (f'"{role}"', place, level, site) if x)
                out.append((q, name))
    return out


def search_jobs(prefs: dict, *, fresh_days: int | None = None, max_jobs: int = 25, max_pages: int = 45,
                searcher: Callable | None = None, fetcher: Callable | None = None, now: datetime | None = None,
                progress: Callable[[str], None] | None = None) -> list[dict]:
    """Live search → verified job records (unverified ones flagged). Newest known postings first."""
    searcher, fetcher = searcher or ddg, fetcher or http_fetch
    now = now or datetime.now(timezone.utc)
    limit = None if not fresh_days else ("d" if fresh_days <= 1 else "w" if fresh_days <= 7 else "m")
    urls: dict[str, dict] = {}
    queries = build_queries(prefs, bool(fresh_days))
    say = progress or (lambda s: None)
    say(f"Searching {len({n for _, n in queries})} job sources…")

    def run(q):
        query, name = q
        return name, searcher(query, 8, limit) or []
    with ThreadPoolExecutor(6) as pool:
        for name, rows in pool.map(run, queries):
            for r in rows:
                u = r.get("url") or r.get("href") or ""
                src, prio = source_of(u)
                if not src:
                    continue
                key = normalize_url(u)
                if key not in urls:
                    urls[key] = {"url": u, "snippet": r.get("snippet", ""), "prio": prio}
    candidates = sorted(urls.values(), key=lambda x: x["prio"])[:max_pages]
    say(f"Found {len(urls)} possible postings — checking {len(candidates)} job pages…")
    with ThreadPoolExecutor(8) as pool:
        jobs = list(pool.map(lambda c: read_posting(c["url"], fetcher, now), candidates))
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
