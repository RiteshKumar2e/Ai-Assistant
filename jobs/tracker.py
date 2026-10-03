"""
jobs/tracker.py — the user's own application history.

One record per job the user acted on. Duplicates are caught by ATS job id, by
normalized application URL, and by company + role, so the same job reached
through LinkedIn and through the company's own page is still one application.
"""
from __future__ import annotations

import re
import time

from jobs.search import external_id, normalize_url
from jobs.store import UserStore

STATUSES = ("DISCOVERED", "MATCHED", "RESUME_READY", "APPLICATION_STARTED", "WAITING_FOR_CONFIRMATION", "SUBMITTED",
            "VERIFIED", "FAILED", "EXPIRED", "ALREADY_APPLIED")
DONE = ("SUBMITTED", "VERIFIED", "ALREADY_APPLIED")
FIELDS = ("company", "role", "job_url", "application_url", "job_id", "source", "posting_date", "resume_used",
          "application_date", "status", "verification_evidence", "notes", "location")


def _norm(s: str) -> str:
    s = re.sub(r"\b(?:inc|llc|ltd|limited|pvt|private|corp|corporation|technologies|technology|labs?)\b\.?", "", (s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


class Tracker:
    def __init__(self, store: UserStore):
        self.store = store

    def all(self) -> list[dict]:
        return self.store.load("applications", [])

    def _save(self, rows: list[dict]) -> None:
        self.store.save("applications", rows)

    def find(self, job: dict) -> dict | None:
        """This user's existing record for the same job, if any."""
        jid = job.get("job_id") or external_id(job.get("url", ""))
        urls = {normalize_url(u) for u in (job.get("url"), job.get("apply_url")) if u}
        company, role = _norm(job.get("company", "")), _norm(job.get("title", ""))
        for r in self.all():
            if jid and r.get("job_id") == jid:
                return r
            if urls & {normalize_url(u) for u in (r.get("job_url"), r.get("application_url")) if u}:
                return r
            if company and role and _norm(r.get("company", "")) == company and _norm(r.get("role", "")) == role:
                return r
        return None

    def already_applied(self, job: dict) -> dict | None:
        r = self.find(job)
        return r if r and r.get("status") in DONE else None

    def upsert(self, job: dict, status: str, **extra) -> dict:
        if status not in STATUSES:
            raise ValueError(status)
        rows = self.all()
        existing = self.find(job)
        now = time.strftime("%Y-%m-%d %H:%M")
        if existing:
            rec = next(r for r in rows if r["key"] == existing["key"])
        else:
            rec = {"key": job.get("id") or normalize_url(job.get("url", "")), "company": job.get("company", ""),
                   "role": job.get("title", ""), "location": job.get("location", ""), "job_url": job.get("url", ""),
                   "application_url": job.get("apply_url", ""), "job_id": job.get("job_id", ""),
                   "source": job.get("source", ""), "posting_date": job.get("posted_at") or "", "resume_used": "",
                   "application_date": "", "status": status, "verification_evidence": "", "notes": "", "history": []}
            rows.append(rec)
        if rec["status"] in DONE and status not in ("VERIFIED", "ALREADY_APPLIED") and status != rec["status"]:
            status = rec["status"]                      # never downgrade a submitted application
        rec["status"] = status
        for k, v in extra.items():
            if k in FIELDS and v not in (None, ""):
                rec[k] = v
        if status in ("SUBMITTED", "VERIFIED") and not rec.get("application_date"):
            rec["application_date"] = now
        rec["history"].append({"at": now, "status": status})
        self._save(rows)
        return rec

    def describe(self, limit: int = 15) -> str:
        rows = self.all()
        if not rows:
            return "You haven't applied to anything through JUDO yet."
        rows = sorted(rows, key=lambda r: r["history"][-1]["at"] if r.get("history") else "", reverse=True)[:limit]
        return "\n".join(f"• {r['company'] or '?'} — {r['role'] or '?'}: {r['status']}"
                         + (f" (applied {r['application_date']})" if r.get("application_date") else "")
                         + (f" — {r['verification_evidence'][:80]}" if r.get("verification_evidence") else "")
                         for r in rows)
