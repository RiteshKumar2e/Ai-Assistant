"""JobAgent — the user's job search and applications, from their own resume (jobs/service.py).

The supervisor can search, match, tailor, fill an application and put the
APPLICATION REVIEW on screen. It cannot submit: "request_submission" returns
CONFIRMATION_PENDING and the application is sent only when the user presses
CONFIRM on the HUD (jobs/apply.py). Results are the service's own words."""
from __future__ import annotations

from agent.agents.base import Agent, int_arg
from agent.tools.base import READ, WRITE, ToolResult


def _res(text: str) -> ToolResult:
    bad = text.startswith(("Please upload your latest resume", "Which job?", "There's no application", "The job agent hit"))
    return ToolResult(not bad, text, status="ok" if not bad else "needs_input")


class JobAgent(Agent):
    name = "JobAgent"
    description = ("Job search & applications from the user's own resume: upload/confirm resume, preferences, live job search, "
                   "resume↔JD matching, tailored resume, filling applications in JUDO Browser, application tracker.")

    def _svc(self):
        from jobs.service import for_user
        svc = for_user()
        if svc.llm is None:
            svc.llm = self.llm
        return svc

    def tools(self):
        s = self._svc
        return [
            self.tool("job_profile_status", "Is a confirmed resume profile on file? Returns what to ask the user first, if anything.",
                      {}, lambda: _res(s().gate() or "Profile confirmed. " + s().resumes()), READ),
            self.tool("upload_resume", "Read the user's resume file (PDF/DOCX/TXT) into their profile.", {"path": "resume file path"},
                      lambda path: _res(s().upload_resume(path)), WRITE),
            self.tool("confirm_job_profile", "The user said their parsed profile is correct.", {},
                      lambda: _res(s().confirm_profile()), WRITE),
            self.tool("set_job_preferences", "Save the user's job preferences (only what they said).",
                      {"roles": "comma list", "locations": "comma list", "remote": "remote|hybrid|onsite|any",
                       "experience_range": "e.g. 0-2", "salary_min": "number", "job_types": "full-time, internship"},
                      lambda **kw: _res(s().set_preferences(kw)), WRITE),
            self.tool("find_jobs", "Live search for jobs matching the user's resume and preferences; verifies pages and matches each.",
                      {"fresh_days": "only posted within N days (optional)", "roles": "comma list (optional)"},
                      lambda fresh_days="", roles="": _res(s().find_jobs(int_arg(fresh_days, 0) or None,
                                                                         [r.strip() for r in roles.split(",") if r.strip()] or None)),
                      READ, long_running=True),
            self.tool("job_details", "Full match reasoning, dates and links for job N of the last search.", {"job": "number"},
                      lambda job: _res(s().details(job)), READ),
            self.tool("tailor_resume", "Make an honest tailored copy of the user's resume for job N (shown to the user).",
                      {"job": "number"}, lambda job: _res(s().tailor(job)), WRITE),
            self.tool("prepare_application", "Open job N's application in JUDO Browser and fill what the profile answers.",
                      {"job": "number"}, lambda job: _res(s().apply(job)), WRITE, long_running=True),
            self.tool("answer_application_question", "Fill the user's OWN answer to an application question.",
                      {"question": "number or words", "answer": "user's words", "job": "number (optional)"},
                      lambda question, answer, job="": _res(s().answer(question, answer, job or None)), WRITE),
            self.tool("request_submission", "Show the APPLICATION REVIEW for the user to CONFIRM on screen (does not submit).",
                      {"job": "number (optional)"}, lambda job="": _res(s().submit(job or None)), WRITE),
            self.tool("job_applications", "The user's application tracker.", {}, lambda: _res(s().applications()), READ),
        ]
