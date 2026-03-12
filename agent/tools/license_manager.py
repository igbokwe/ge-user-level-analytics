"""
License Manager tool: uses the Google Workspace Licensing API to check and
revoke Gemini Enterprise (SKU) licenses for individual users.

Authentication: Google Identity for Agents (ADK ToolContext OAuth flow).
The agent acts on behalf of the user who invokes it.  On the first call the
ADK triggers a Google OAuth consent screen in the Gemini Enterprise UI; on
subsequent calls the stored access token is retrieved from session state.

The authorising user must be a Google Workspace super-admin so they can read
and manage licenses across the domain.

Required OAuth scopes (requested via _auth.py):
  - https://www.googleapis.com/auth/apps.licensing
  - https://www.googleapis.com/auth/admin.directory.user.readonly
"""

from __future__ import annotations

import os
from typing import Any

from google.adk.tools.tool_context import ToolContext
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from agent.tools._auth import get_workspace_credentials
from agent.tools.trace import tracer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PRODUCT_ID = os.environ.get("GEMINI_ENTERPRISE_PRODUCT_ID", "Google-Gemini-Enterprise")
_DEFAULT_SKU_ID = os.environ.get("GEMINI_ENTERPRISE_SKU_ID", "1010310006")

_AUTH_REQUIRED = {
    "error": "authentication_required",
    "message": (
        "Google Workspace access is required. "
        "Please authorise the agent via the consent screen that has appeared."
    ),
}


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------


def _licensing_service(tool_context: ToolContext):
    creds = get_workspace_credentials(tool_context)
    if creds is None:
        return None
    tracer.log("license_credentials", "using caller identity (Google Identity for Agents)")
    return build("licensing", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def get_user_license_status(user_email: str, tool_context: ToolContext) -> dict[str, Any]:
    """
    Check whether a user currently holds a Gemini Enterprise license.

    Args:
        user_email:   The user's Google Workspace email address.
        tool_context: Injected by ADK — carries the caller's identity token.

    Returns:
        A dict with keys: user, has_license, product_id, sku_id, error.
    """
    with tracer.span("get_user_license_status", user_email=user_email) as span:
        product_id = _DEFAULT_PRODUCT_ID
        sku_id = _DEFAULT_SKU_ID

        service = _licensing_service(tool_context)
        if service is None:
            span.ok(has_license=False, reason="auth_required")
            return {**_AUTH_REQUIRED, "user": user_email, "has_license": False,
                    "product_id": product_id, "sku_id": sku_id}

        tracer.log(
            "get_user_license_status",
            "checking license via Workspace Licensing API",
            user_email=user_email, product_id=product_id, sku_id=sku_id,
        )

        try:
            result = (
                service.licenseAssignments()
                .get(productId=product_id, skuId=sku_id, userId=user_email)
                .execute()
            )
            tracer.log("get_user_license_status", "license found",
                       user_email=user_email, api_result=result)
            out = {
                "user": user_email,
                "has_license": True,
                "product_id": result.get("productId"),
                "sku_id": result.get("skuId"),
                "error": None,
            }
            span.ok(has_license=True)
            return out
        except HttpError as exc:
            status_code = exc.resp.status
            tracer.log("get_user_license_status", "HttpError from Licensing API",
                       user_email=user_email, http_status=status_code, error=str(exc))
            if status_code == 404:
                out = {
                    "user": user_email, "has_license": False,
                    "product_id": product_id, "sku_id": sku_id, "error": None,
                }
                span.ok(has_license=False, reason="404_not_found")
                return out
            out = {
                "user": user_email, "has_license": False,
                "product_id": product_id, "sku_id": sku_id, "error": str(exc),
            }
            span.ok(has_license=False, reason="api_error", http_status=status_code)
            return out


def revoke_gemini_license(
    user_email: str, tool_context: ToolContext, dry_run: bool = False
) -> dict[str, Any]:
    """
    Revoke the Gemini Enterprise license for the given user.

    Args:
        user_email:   The user's Google Workspace email address.
        tool_context: Injected by ADK — carries the caller's identity token.
        dry_run:      If True, simulate without making API calls.

    Returns:
        A dict with keys: user, revoked, dry_run, message, error.
    """
    with tracer.span("revoke_gemini_license", user_email=user_email, dry_run=dry_run) as span:
        product_id = _DEFAULT_PRODUCT_ID
        sku_id = _DEFAULT_SKU_ID

        # Verify the license exists first (also validates auth)
        status = get_user_license_status(user_email, tool_context)
        if status.get("error") == "authentication_required":
            span.ok(revoked=False, reason="auth_required")
            return {**status, "user": user_email, "revoked": False, "dry_run": dry_run}

        tracer.log("revoke_gemini_license", "license status check result",
                   has_license=status["has_license"], status_error=status.get("error"))

        if not status["has_license"]:
            out = {
                "user": user_email, "revoked": False, "dry_run": dry_run,
                "message": "User does not hold a Gemini Enterprise license; no action taken.",
                "error": status.get("error"),
            }
            span.ok(revoked=False, reason="no_license")
            return out

        if dry_run:
            tracer.log("revoke_gemini_license", "dry_run mode — skipping actual revocation")
            out = {
                "user": user_email, "revoked": False, "dry_run": True,
                "message": f"[DRY RUN] Would revoke {product_id}/{sku_id} from {user_email}.",
                "error": None,
            }
            span.ok(revoked=False, reason="dry_run")
            return out

        service = _licensing_service(tool_context)
        if service is None:
            span.ok(revoked=False, reason="auth_required")
            return {**_AUTH_REQUIRED, "user": user_email, "revoked": False, "dry_run": False}

        tracer.log("revoke_gemini_license", "calling Licensing API delete",
                   product_id=product_id, sku_id=sku_id, user_email=user_email)
        try:
            service.licenseAssignments().delete(
                productId=product_id, skuId=sku_id, userId=user_email
            ).execute()
            tracer.log("revoke_gemini_license", "license successfully deleted",
                       user_email=user_email)
            out = {
                "user": user_email, "revoked": True, "dry_run": False,
                "message": f"Successfully revoked Gemini Enterprise license from {user_email}.",
                "error": None,
            }
            span.ok(revoked=True)
            return out
        except HttpError as exc:
            tracer.log("revoke_gemini_license", "HttpError during revocation",
                       user_email=user_email, http_status=exc.resp.status, error=str(exc))
            out = {
                "user": user_email, "revoked": False, "dry_run": False,
                "message": "License revocation failed.", "error": str(exc),
            }
            span.ok(revoked=False, reason="api_error", http_status=exc.resp.status)
            return out


def list_all_licensed_users(tool_context: ToolContext) -> dict[str, Any]:
    """
    List every user currently assigned a Gemini Enterprise license in the domain.

    Args:
        tool_context: Injected by ADK — carries the caller's identity token.

    Returns:
        A dict with key "licensed_users": list of {user, product_id, sku_id}.
    """
    with tracer.span("list_all_licensed_users") as span:
        product_id = _DEFAULT_PRODUCT_ID
        sku_id = _DEFAULT_SKU_ID
        workspace_domain = os.environ.get("WORKSPACE_DOMAIN", "<not set>")

        service = _licensing_service(tool_context)
        if service is None:
            span.ok(licensed_count=0, reason="auth_required")
            return {**_AUTH_REQUIRED, "licensed_users": []}

        tracer.log("list_all_licensed_users", "listing all licensed users",
                   product_id=product_id, sku_id=sku_id, workspace_domain=workspace_domain)

        licensed: list[dict[str, str]] = []
        page_token: str | None = None
        page_num = 0

        while True:
            page_num += 1
            kwargs: dict[str, Any] = {
                "productId": product_id,
                "skuId": sku_id,
                "customerId": os.environ["WORKSPACE_DOMAIN"],
                "maxResults": 1000,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            tracer.log("list_all_licensed_users", "fetching page",
                       page_num=page_num, has_page_token=bool(page_token))

            try:
                response = service.licenseAssignments().listForProductAndSku(**kwargs).execute()
            except HttpError as exc:
                tracer.log("list_all_licensed_users", "HttpError fetching page",
                           page_num=page_num, http_status=exc.resp.status, error=str(exc))
                out = {"licensed_users": licensed, "error": str(exc)}
                span.ok(licensed_count=len(licensed), error=str(exc))
                return out

            items = response.get("items", [])
            tracer.log("list_all_licensed_users", "page received",
                       page_num=page_num, items_on_page=len(items))

            for item in items:
                licensed.append({
                    "user": item.get("userId", ""),
                    "product_id": item.get("productId", ""),
                    "sku_id": item.get("skuId", ""),
                })

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        span.ok(licensed_count=len(licensed))
        return {"licensed_users": licensed}
