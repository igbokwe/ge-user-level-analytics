"""
Log Analytics tool: queries Cloud Logging (BigQuery-backed) to find
inactive Gemini Enterprise users based on Discovery Engine API audit logs.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_DISCOVERY_ENGINE_SERVICE = "discoveryengine.googleapis.com"

_ACTIVE_METHODS = [
    "google.cloud.discoveryengine.v1main.AssistantService.StreamAssist",
    "google.cloud.discoveryengine.v1main.SearchService.Search",
    "google.cloud.discoveryengine.v1.AssistantService.StreamAssist",
    "google.cloud.discoveryengine.v1.SearchService.Search",
]


def _get_bq_client() -> bigquery.Client:
    project_id = os.environ["GCP_PROJECT_ID"]
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path:
        credentials = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return bigquery.Client(project=project_id, credentials=credentials)
    return bigquery.Client(project=project_id)


def _log_table(project_id: str) -> str:
    bucket = os.environ.get("LOG_BUCKET", "_Default")
    view = os.environ.get("LOG_VIEW", "_AllLogs")
    return f"`{project_id}.global.{bucket}.{view}`"


# ---------------------------------------------------------------------------
# Public tool functions (called by the ADK agent)
# ---------------------------------------------------------------------------


def query_inactive_users(inactivity_days: int = 45) -> dict[str, Any]:
    """
    Query Cloud Log Analytics for users who have not interacted with the
    Gemini Enterprise (Discovery Engine) API within `inactivity_days` days.

    Args:
        inactivity_days: Number of days of inactivity that triggers revocation.
                         Defaults to 45.

    Returns:
        A dict with keys:
          - inactive_users: list of {"user": str, "last_activity": str (ISO date)}
          - threshold_date: ISO date string used as the cutoff
          - queried_at: ISO timestamp of when the query ran
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    table = _log_table(project_id)
    threshold = date.today() - timedelta(days=inactivity_days)

    sql = f"""
        SELECT
            proto_payload.audit_log.authentication_info.principal_email AS user,
            MAX(DATE(timestamp)) AS last_activity
        FROM
            {table}
        WHERE
            proto_payload.audit_log.service_name = @service_name
        GROUP BY
            1
        HAVING
            MAX(DATE(timestamp)) < @threshold
            OR MAX(DATE(timestamp)) IS NULL
        ORDER BY
            last_activity ASC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
            bigquery.ScalarQueryParameter("threshold", "DATE", threshold.isoformat()),
        ]
    )

    client = _get_bq_client()
    rows = client.query(sql, job_config=job_config).result()

    inactive: list[dict[str, str]] = []
    for row in rows:
        email = row["user"]
        last_act = row["last_activity"]
        # Skip service accounts and internal system principals
        if not email or email.endswith(".gserviceaccount.com"):
            continue
        inactive.append(
            {
                "user": email,
                "last_activity": last_act.isoformat() if last_act else "never",
            }
        )

    return {
        "inactive_users": inactive,
        "threshold_date": threshold.isoformat(),
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


def query_user_last_activity(user_email: str) -> dict[str, Any]:
    """
    Return the last activity date and method for a specific user.

    Args:
        user_email: The principal email address to look up.

    Returns:
        A dict with keys:
          - user: str
          - last_activity: ISO date string or "never"
          - last_method: most recent API method called, or null
          - days_since_activity: int
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    table = _log_table(project_id)

    sql = f"""
        SELECT
            proto_payload.audit_log.authentication_info.principal_email AS user,
            MAX(DATE(timestamp))                                         AS last_activity,
            ARRAY_AGG(
                proto_payload.audit_log.method_name
                ORDER BY timestamp DESC
                LIMIT 1
            )[OFFSET(0)]                                                 AS last_method
        FROM
            {table}
        WHERE
            proto_payload.audit_log.service_name = @service_name
            AND proto_payload.audit_log.authentication_info.principal_email = @email
        GROUP BY
            1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
            bigquery.ScalarQueryParameter("email", "STRING", user_email),
        ]
    )

    client = _get_bq_client()
    rows = list(client.query(sql, job_config=job_config).result())

    if not rows:
        return {
            "user": user_email,
            "last_activity": "never",
            "last_method": None,
            "days_since_activity": None,
        }

    row = rows[0]
    last_act: date | None = row["last_activity"]
    days_since = (date.today() - last_act).days if last_act else None

    return {
        "user": user_email,
        "last_activity": last_act.isoformat() if last_act else "never",
        "last_method": row["last_method"],
        "days_since_activity": days_since,
    }


def query_daily_usage(days_back: int = 30) -> dict[str, Any]:
    """
    Return a daily usage breakdown per user and API method for reporting.

    Args:
        days_back: Number of past days to include in the report. Default 30.

    Returns:
        A dict with key "usage_records": list of
          {"date": str, "user": str, "method": str, "activity_count": int}
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    table = _log_table(project_id)
    since = date.today() - timedelta(days=days_back)

    sql = f"""
        SELECT
            DATE(timestamp)                                                AS activity_date,
            proto_payload.audit_log.authentication_info.principal_email   AS user,
            proto_payload.audit_log.method_name                           AS method,
            COUNT(1)                                                       AS activity_count
        FROM
            {table}
        WHERE
            proto_payload.audit_log.service_name = @service_name
            AND DATE(timestamp) >= @since
        GROUP BY
            1, 2, 3
        ORDER BY
            1 DESC, 4 DESC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
            bigquery.ScalarQueryParameter("since", "DATE", since.isoformat()),
        ]
    )

    client = _get_bq_client()
    rows = client.query(sql, job_config=job_config).result()

    records = [
        {
            "date": row["activity_date"].isoformat(),
            "user": row["user"],
            "method": row["method"],
            "activity_count": row["activity_count"],
        }
        for row in rows
        if row["user"] and not row["user"].endswith(".gserviceaccount.com")
    ]

    return {"usage_records": records}
