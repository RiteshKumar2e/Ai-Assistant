"""
jobs — JUDO's Job Search & Job Application Agent, for whoever is using JUDO.

Nothing in here knows about any particular person. Every resume, profile,
preference, discovered job and application record lives in that user's own
store (jobs/store.py), and every decision is made from that user's resume:

    resume upload → parse → profile (user confirms) → preferences
    → live search → verify job pages → resume ↔ JD match → tailor (optional, shown first)
    → fill the real application in JUDO Browser → review → CONFIRM on screen
    → submit → verify from the page → tracker

The final submit can only run from inside a core/confirm.py callback — the
on-screen CONFIRM button — never from a model's tool call (jobs/apply.py).
"""
