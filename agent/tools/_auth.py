"""
Shared Google Workspace authentication for agent tools.

Primary path: ADMIN_REFRESH_TOKEN env var (pre-authorised admin credentials
stored at deploy time).  This bypasses the GE per-user consent screen, which
does not reliably surface for the OpenIdConnectWithConfig scheme.

Fallback path: ADK ToolContext OAuth flow (request_credential / get_auth_response).
This is kept as a fallback in case the env var is not set.

Required env vars:
  ADMIN_REFRESH_TOKEN  – OAuth refresh token for admin@<domain> (recommended)
  OAUTH_CLIENT_ID      – OAuth 2.0 client ID
  OAUTH_CLIENT_SECRET  – matching client secret
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
# Primary path: pre-configured admin refresh token
# ---------------------------------------------------------------------------


def _get_admin_credentials():
    """Return credentials built from the ADMIN_REFRESH_TOKEN env var, or None."""
    refresh_token = os.environ.get("ADMIN_REFRESH_TOKEN", "")
    client_id = os.environ.get("OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")

    if not (refresh_token and client_id and client_secret):
        return None

    from google.oauth2.credentials import Credentials  # noqa: PLC0415

    return Credentials(
        token=None,  # Will be refreshed automatically on first use
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=WORKSPACE_SCOPES,
    )


# ---------------------------------------------------------------------------
# Fallback path: ADK ToolContext OAuth flow
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
# Public helper
# ---------------------------------------------------------------------------


def get_workspace_credentials(tool_context: ToolContext):
    """Return a google.oauth2.credentials.Credentials for Workspace API calls.

    Tries ADMIN_REFRESH_TOKEN first, then falls back to ADK OAuth flow.
    Returns None (and triggers the consent flow) if neither is available.
    """
    # Primary: pre-configured admin credentials (no consent screen required)
    creds = _get_admin_credentials()
    if creds is not None:
        return creds

    # Fallback: per-user ADK OAuth flow
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


def request_workspace_auth(tool_context) -> bool:
    """Return True if credentials are available, False if consent was requested.

    Must be called from a tool function (ToolContext only).
    """
    if _get_admin_credentials() is not None:
        return True

    auth_config = _workspace_auth_config()
    auth_credential = tool_context.get_auth_response(auth_config)

    if auth_credential and auth_credential.oauth2 and auth_credential.oauth2.access_token:
        return True

    tool_context.request_credential(auth_config)
    return False
