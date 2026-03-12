"""
Shared Google Identity for Agents authentication configuration.

Tools that need to call Google Workspace APIs (Licensing, Directory, Gmail)
use the ADK ToolContext OAuth flow so the agent acts on behalf of the user
who is currently running it — not a pre-baked service account or refresh token.

Flow (handled automatically by ADK + Gemini Enterprise):
  1. Tool calls `get_workspace_credentials(tool_context)`.
  2. If no credentials are cached in the session yet, the call to
     `tool_context.request_credential()` signals GE to show the user a
     Google OAuth consent screen for the required Workspace scopes.
  3. After the user grants access, ADK retries the tool with the token
     stored in session state; `tool_context.get_auth_response()` returns it.
  4. A `google.oauth2.credentials.Credentials` object is returned so the
     caller can pass it directly to any `googleapiclient.discovery.build()`.

Required env vars (set once at deploy time):
  OAUTH_CLIENT_ID     – OAuth 2.0 client ID for the consent screen
  OAUTH_CLIENT_SECRET – matching client secret
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
# AuthConfig – uses Google's OIDC discovery endpoints
# ---------------------------------------------------------------------------

# Built lazily so that OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET are read at
# call time rather than at import time (env vars are set by Agent Engine).
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
# Public helper
# ---------------------------------------------------------------------------


def get_workspace_credentials(tool_context: ToolContext):
    """Return a google.oauth2.credentials.Credentials for Workspace API calls.

    Returns ``None`` and triggers the OAuth consent flow when the user has not
    yet granted access.  The caller must check for ``None`` and return an
    appropriate "authentication required" response to the agent.
    """
    from google.oauth2.credentials import Credentials  # noqa: PLC0415

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

    # No token yet — ask GE to show the user a consent screen
    tool_context.request_credential(auth_config)
    return None


def request_workspace_auth(context) -> bool:
    """Eagerly request Workspace OAuth credentials from the invoking user.

    Can be called from a ``before_agent_callback`` (``CallbackContext``) or a
    tool function (``ToolContext``) — both expose ``request_credential`` and
    ``get_auth_response``.

    Returns:
        True  — credentials are already cached; no consent screen needed.
        False — ``request_credential()`` was called; the ADK framework will
                pause the agent and show the user a Google consent screen.
    """
    auth_config = _workspace_auth_config()
    auth_credential = context.get_auth_response(auth_config)

    if auth_credential and auth_credential.oauth2 and auth_credential.oauth2.access_token:
        return True

    context.request_credential(auth_config)
    return False
