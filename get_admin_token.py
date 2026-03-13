#!/usr/bin/env python3
"""
One-time script to get a Workspace admin refresh token.

Run this LOCALLY (not on the server) as admin@igbokwe.altostrat.com:
    python3 get_admin_token.py

It will open your browser, ask you to grant Workspace access, then print
the refresh token.  Copy the token and add it to .env:
    ADMIN_REFRESH_TOKEN=<token>
Then redeploy the agent.

Requirements: pip install google-auth-oauthlib
"""

import json

CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"
SCOPES = [
    "https://www.googleapis.com/auth/apps.licensing",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Install the required library first:")
    print("    pip install google-auth-oauthlib")
    raise

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
creds = flow.run_local_server(port=0, login_hint="admin@igbokwe.altostrat.com", prompt="consent")

print("\n" + "=" * 60)
print("SUCCESS! Add this to your .env file:")
print("=" * 60)
print(f"ADMIN_REFRESH_TOKEN={creds.refresh_token}")
print("=" * 60)
