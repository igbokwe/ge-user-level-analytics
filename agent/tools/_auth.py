"""
Shared Google Workspace authentication for agent tools.

Tools that need to call Google Workspace APIs (Licensing, Directory, Gmail)
support two authentication paths (tried in order):

Path 1 — ADK ToolContext OAuth (per-user consent in GE):
  1. Tool calls `get_workspace_credentials(tool_context)`.
  2. If no credentials are cached in the session, `tool_context.request_credential()`
     signals GE to generate a Google OAuth consent URL (requires `authlib` to be
     installed in the deployment — it is included in deploy.py requirements).
  3. GE shows the user an "Authorize" button.  After the user grants access, ADK
     exchanges the auth code, stores the token in session state, and the next
     tool call returns a valid Credentials object via `get_auth_response()`.

Path 2 — Pre-stored admin refresh token (one-time setup):
  If WORKSPACE_ADMIN_REFRESH_TOKEN is set in the environment, the agent uses
  that refresh token directly without requiring a per-session consent flow.
  Run `python get_admin_token.py` once locally to obtain the token, then set:
    WORKSPACE_ADMIN_REFRESH_TOKEN=<token>  in your .env / deployment env.

Required env vars:
  OAUTH_CLIENT_ID              – OAuth 2.0 Web/Desktop client ID
  OAUTH_CLIENT_SECRET          – matching client secret
  WORKSPACE_ADMIN_REFRESH_TOKEN – (optional) pre-stored admin refresh token
"""

from __future__ import annotations

import os

from google.adk.auth import AuthConfig, AuthCredential, AuthCredentialTypes, OAuth2Auth
from google.adk.auth.auth_schemes import OpenIdConnectWithConfig
from google.adk.tools.tool_context import ToolContext

# ---------------------------------------------------------------------------
# All Workspace scopes required by any tool in this agent
# ---------------------------------------------------------------------------

WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/apps.licensing",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# ---------------------------------------------------------------------------
# AuthConfig – built lazily so env vars are read at call time
# ---------------------------------------------------------------------------


def _workspace_auth_config() -> AuthConfig:
    return AuthConfig(
        auth_scheme=OpenIdConnectWithConfig(
            authorization_endpoint="https://accounts.google.com/o/oauth2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            scopes=WORKSPACE_SCOPES,
        ),
        raw_auth_credential=AuthCredential(
            auth_type=AuthCredentialTypes.OAUTH2,
            oauth2=OAuth2Auth(
                client_id=os.environ.get("OAUTH_CLIENT_ID", ""),
                client_secret=os.environ.get("OAUTH_CLIENT_SECRET", ""),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_workspace_credentials(tool_context: ToolContext):
    """Return a google.oauth2.credentials.Credentials for Workspace API calls.

    Tries Path 1 (ADK per-user OAuth) first, then falls back to Path 2
    (pre-stored WORKSPACE_ADMIN_REFRESH_TOKEN).  Returns None only when
    neither path is available, which also triggers the GE consent prompt.
    """
    from google.oauth2.credentials import Credentials  # noqa: PLC0415

    # --- Path 1: ADK per-user OAuth (GE consent button) ---
    auth_config = _workspace_auth_config()
    auth_credential = tool_context.get_auth_response(auth_config)

    if auth_credential and auth_credential.oauth2 and auth_credential.oauth2.access_token:
        return Credentials(
            token=auth_credential.oauth2.access_token,
            refresh_token=auth_credential.oauth2.refresh_token,
            client_id=os.environ.get("OAUTH_CLIENT_ID"),
            client_secret=os.environ.get("OAUTH_CLIENT_SECRET"),
            token_uri="https://oauth2.googleapis.com/token",
            scopes=WORKSPACE_SCOPES,
        )

    # --- Path 2: pre-stored admin refresh token ---
    admin_refresh_token = os.environ.get("WORKSPACE_ADMIN_REFRESH_TOKEN", "")
    if admin_refresh_token:
        return Credentials(
            token=None,
            refresh_token=admin_refresh_token,
            client_id=os.environ.get("OAUTH_CLIENT_ID"),
            client_secret=os.environ.get("OAUTH_CLIENT_SECRET"),
            token_uri="https://oauth2.googleapis.com/token",
            scopes=WORKSPACE_SCOPES,
        )

    # --- No credentials available: trigger the GE OAuth consent prompt ---
    tool_context.request_credential(auth_config)
    return None


def request_workspace_auth(tool_context: ToolContext) -> bool:
    """Return True if credentials are already available, False if consent was requested.

    Must be called from a ToolContext (not a CallbackContext).
    """
    auth_config = _workspace_auth_config()
    auth_credential = tool_context.get_auth_response(auth_config)

    if auth_credential and auth_credential.oauth2 and auth_credential.oauth2.access_token:
        return True

    tool_context.request_credential(auth_config)
    return False
