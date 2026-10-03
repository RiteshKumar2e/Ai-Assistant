"""
plugins/job_agent.py — JUDO's Job Search & Application Agent, by voice or text.

Resume first: until the user has uploaded (and confirmed) their own resume, every
job request is answered with a request for the resume. All data is the current
user's own (jobs/store.py). Submitting is never an action here — "submit" only
puts the APPLICATION REVIEW on screen, and the application is sent when the user
presses CONFIRM (jobs/apply.py, core/confirm.py).
"""
from __future__ import annotations

PLUGIN = {
    "name": "job_agent",
    "description": (
        "Job search and job applications for the user, based on THEIR OWN resume. Use for: find / search jobs, fresh "
        "jobs, jobs matching my resume, upload or change my resume, confirm or edit my job profile, job preferences "
        "(roles, locations, remote, salary, experience, internships), job details, tailor my resume for a job, apply to "
        "a job, answer application questions, review or submit an application, my applications / tracker. "
        "When the user uploads a resume file or says it's their resume, call action=upload_resume. "
        "Never invent profile details or answers to application questions — pass the user's own words. "
        "action=submit only shows the review for the user to CONFIRM on screen; it does not submit by itself. "
        "Do NOT use for general web search (web_search) or email (send_email)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": (
                "find_jobs | upload_resume | confirm_profile | edit_profile | show_profile | list_resumes | select_resume | "
                "manual_profile | set_preferences | list_jobs | job_details | tailor_resume | use_tailored_resume | apply | "
                "apply_anyway | continue_application | answer_question | review_application | submit | applications | open_window")},
            "job": {"type": "STRING", "description": "Job number from the last list (e.g. '2'), or company / title words"},
            "fresh_days": {"type": "INTEGER", "description": "Only jobs posted in the last N days (1 = today, 3, 7)"},
            "roles": {"type": "STRING", "description": "Comma-separated roles, e.g. 'Software Engineer, Backend Developer'"},
            "locations": {"type": "STRING", "description": "Comma-separated places, e.g. 'Bengaluru, India' or 'Remote'"},
            "remote": {"type": "STRING", "description": "remote | hybrid | onsite | any"},
            "experience": {"type": "STRING", "description": "Experience range in years the user wants, e.g. '0-2'"},
            "salary_min": {"type": "STRING", "description": "Minimum salary the user stated"},
            "internships": {"type": "STRING", "description": "yes / no — include internships"},
            "job_types": {"type": "STRING", "description": "full-time, internship, contract…"},
            "international_remote": {"type": "STRING", "description": "yes / no"},
            "file_path": {"type": "STRING", "description": "Resume file path (PDF/DOCX/TXT). Omit to use the file uploaded to JUDO."},
            "resume": {"type": "STRING", "description": "Which resume to use (number or name) for select_resume"},
            "question": {"type": "STRING", "description": "Application question number or words, for answer_question"},
            "answer": {"type": "STRING", "description": "The user's own answer (or 'skip')"},
            "remember": {"type": "STRING", "description": "yes to reuse this answer on future applications"},
            "changes": {"type": "STRING", "description": "Profile corrections as 'field: value; field: value' (name, email, phone, "
                                                          "location, add_skills, remove_skills, experience_years, roles, work_authorization)"},
            "show_all": {"type": "STRING", "description": "yes to include jobs that aren't a fit"},
        },
        "required": ["action"],
    },
}


def _yes(v) -> bool:
    return str(v or "").strip().lower() in ("yes", "y", "true", "1", "on")


def _changes(text: str) -> dict:
    out = {}
    for part in str(text or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower().replace(" ", "_")] = v.strip()
    return out


def _service(player):
    from jobs.service import for_user
    log = (lambda s: player.write_log(s)) if player and hasattr(player, "write_log") else None
    svc = for_user(log=log)
    if svc.llm is None:
        try:
            from agent.llm import LLM
            svc.llm = LLM()
        except Exception:
            pass
    return svc


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "find_jobs")).strip().lower()
    try:
        svc = _service(player)
        job = p.get("job")
        progress = (lambda s: player.write_log(f"JUDO: {s}")) if player and hasattr(player, "write_log") else None
        if action == "upload_resume":
            path = p.get("file_path")
            if not path and player is not None:
                cur = getattr(player, "current_file", None)
                path = cur() if callable(cur) else cur
            return svc.upload_resume(path or "")
        if action == "find_jobs":
            roles = [r.strip() for r in str(p.get("roles") or "").split(",") if r.strip()] or None
            if any(p.get(k) for k in ("locations", "remote", "experience", "salary_min", "internships", "job_types")):
                _prefs(svc, p)
            days = p.get("fresh_days")
            return svc.find_jobs(int(days) if str(days or "").strip().isdigit() else None, roles=roles, progress=progress)
        if action == "set_preferences":
            return _prefs(svc, p)
        if action == "confirm_profile":
            return svc.confirm_profile()
        if action == "edit_profile":
            return svc.edit_profile(_changes(p.get("changes")))
        if action == "manual_profile":
            return svc.create_manual_profile(_changes(p.get("changes")))
        if action == "show_profile":
            return svc.show_profile()
        if action == "list_resumes":
            return svc.resumes()
        if action == "select_resume":
            return svc.select_resume(str(p.get("resume") or ""))
        if action == "list_jobs":
            return svc.list_jobs(show_all=_yes(p.get("show_all")))
        if action == "job_details":
            return svc.details(job)
        if action == "tailor_resume":
            return svc.tailor(job)
        if action == "use_tailored_resume":
            return svc.approve_tailored(job)
        if action == "apply":
            return svc.apply(job)
        if action == "apply_anyway":
            return svc.apply_anyway(job)
        if action == "continue_application":
            return svc.continue_application(job)
        if action == "answer_question":
            return svc.answer(p.get("question"), p.get("answer", ""), job, remember=_yes(p.get("remember")))
        if action == "review_application":
            return svc.review(job)
        if action == "submit":
            return svc.submit(job)
        if action == "applications":
            return svc.applications()
        if action == "open_window":
            from jobs.window import launch
            return launch()
        return f"Unknown job action '{action}'."
    except Exception as e:
        return f"The job agent hit a problem: {e}"


def _prefs(svc, p: dict) -> str:
    prefs = {"roles": p.get("roles"), "locations": p.get("locations"), "remote": p.get("remote"),
             "experience_range": p.get("experience"), "salary_min": p.get("salary_min"),
             "job_types": p.get("job_types")}
    if p.get("internships") not in (None, ""):
        prefs["include_internships"] = _yes(p["internships"])
    if p.get("international_remote") not in (None, ""):
        prefs["international_remote"] = _yes(p["international_remote"])
    return svc.set_preferences(prefs)
