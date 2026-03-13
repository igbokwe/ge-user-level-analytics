"""
One-time helper: obtain a Workspace admin refresh token for the deployed agent.

The agent needs a refresh token with the following Workspace API scopes to
manage Gemini Enterprise licences, list directory users, and send email:
  - https://www.googleapis.com/auth/apps.licensing
  - https://www.googleapis.com/auth/admin.directory.user.readonly
  - https://www.googleapis.com/auth/gmail.send

Run this script ONCE locally as a Workspace super-admin:
    python get_admin_token.py

It will open a browser for Google consent, then print the refresh token.
Add the token to your .env file:
    WORKSPACE_ADMIN_REFRESH_TOKEN=<printed token>

Then redeploy:
    python deployment/deploy.py deploy

Prerequisites
-------------
1. Create an OAuth 2.0 client in your GCP project:
   - Go to: GCP Console → APIs & Services → Credentials
   - Click "Create Credentials" → "OAuth 2.0 Client ID"
   - Application type: "Desktop app"  (or "Web application" with localhost redirect)
   - Download the JSON or copy the client ID and secret

2. Configure the OAuth consent screen as "Internal":
   - Go to: GCP Console → APIs & Services → OAuth consent screen
   - User Type: Internal  (avoids need for Google app verification)
   - Add scopes: apps.licensing, admin.directory.user.readonly, gmail.send

3. Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET in your .env (or export them).

The token never expires as long as it is used at least once every 6 months.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/apps.licensing",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def main() -> None:
    client_id = os.environ.get("OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("ERROR: OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET must be set in .env")
        print()
        print("Steps to create your own OAuth client:")
        print("  1. GCP Console → APIs & Services → Credentials")
        print("  2. Create Credentials → OAuth 2.0 Client ID → Desktop app")
        print("  3. Copy the client ID and secret into .env")
        print("  4. GCP Console → APIs & Services → OAuth consent screen")
        print("     Set User Type to 'Internal' and add the required scopes")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415
    except ImportError:
        print("ERROR: google-auth-oauthlib is not installed.")
        print("Run: pip install google-auth-oauthlib")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    print("Opening browser for Google Workspace authorization...")
    print("Sign in as a Workspace super-admin and approve all requested scopes.")
    print()

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    print()
    print("=" * 60)
    print("SUCCESS — add this to your .env file:")
    print()
    print(f"WORKSPACE_ADMIN_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("Then redeploy the agent:")
    print("  python deployment/deploy.py deploy")
    print("=" * 60)


if __name__ == "__main__":
    main()
