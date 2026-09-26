"""
agent/integrations/google_oauth.py — one Google OAuth sign-in for Gmail + Calendar.

Setup (once):
  1. Google Cloud Console → create an OAuth client of type "Desktop app",
     enable the Gmail API and Google Calendar API, download the JSON.
  2. Save it as config/client_secret_google.json  (gitignored: client_secret*.json)
  3. python -m agent.integrations.google_oauth
     A browser window opens on Google's own consent page; the resulting token
     is saved to config/token_google.json (gitignored: token*.json).

No password is ever seen or stored. The token file is read only by the Google
client library here — it is never shown to the model or put in an event. A
task never starts an interactive sign-in: when there's no token, the tools say
"not connected" and name this command.
"""
from __future__ import annotations

import sys

from agent.config import BASE_DIR

CLIENT_FILE = BASE_DIR / "config" / "client_secret_google.json"
TOKEN_FILE  = BASE_DIR / "config" / "token_google.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/calendar.readonly"]
SETUP_HINT = "run `python -m agent.integrations.google_oauth` after saving your OAuth client as config/client_secret_google.json"


def credentials():
    """Valid credentials or None. Refreshes an expired token silently."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None
    if not TOKEN_FILE.is_file():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return creds if creds.valid else None
    except Exception as e:
        print(f"[Google] token unusable: {e}")
        return None


def status() -> tuple[bool, str]:
    try:
        import googleapiclient  # noqa: F401
    except ImportError:
        return False, "google-api-python-client is not installed (pip install -r requirements.txt)."
    if credentials():
        return True, ""
    return False, f"Google account isn't connected — {SETUP_HINT}."


def service(api: str, version: str):
    from googleapiclient.discovery import build
    creds = credentials()
    if not creds:
        raise RuntimeError(f"Google account isn't connected — {SETUP_HINT}.")
    return build(api, version, credentials=creds, cache_discovery=False)


def login() -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CLIENT_FILE.is_file():
        print(f"Missing {CLIENT_FILE}. Create a Desktop OAuth client in Google Cloud Console and save its JSON there.")
        return 1
    creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES).run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"Connected. Token saved to {TOKEN_FILE} (keep it private; it is gitignored).")
    return 0


if __name__ == "__main__":
    sys.exit(login())
