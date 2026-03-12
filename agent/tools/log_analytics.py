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

from agent.tools.trace import tracer

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
    tracer.log("bq_client", "building BigQuery client", project_id=project_id, creds_path=creds_path)
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
    table = f"`{project_id}.global.{bucket}.{view}`"
    tracer.log("log_table", "resolved log table", table=table)
    return table


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
    with tracer.span("query_inactive_users", inactivity_days=inactivity_days) as span:
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

        tracer.log(
            "query_inactive_users",
            "executing BigQuery SQL",
            project_id=project_id,
            table=table,
            threshold_date=threshold.isoformat(),
            service_name=_DISCOVERY_ENGINE_SERVICE,
            sql=sql.strip(),
        )

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
                bigquery.ScalarQueryParameter("threshold", "DATE", threshold.isoformat()),
            ]
        )

        client = _get_bq_client()
        job = client.query(sql, job_config=job_config)
        tracer.log("query_inactive_users", "BigQuery job submitted", job_id=job.job_id)

        rows = list(job.result())
        tracer.log(
            "query_inactive_users",
            "BigQuery job complete",
            job_id=job.job_id,
            total_rows=len(rows),
        )

        inactive: list[dict[str, str]] = []
        skipped_service_accounts = 0
        skipped_empty = 0

        for row in rows:
            email = row["user"]
            last_act = row["last_activity"]
            if not email:
                skipped_empty += 1
                continue
            if email.endswith(".gserviceaccount.com"):
                skipped_service_accounts += 1
                continue
            inactive.append(
                {
                    "user": email,
                    "last_activity": last_act.isoformat() if last_act else "never",
                }
            )

        tracer.log(
            "query_inactive_users",
            "filtering complete",
            total_rows=len(rows),
            inactive_count=len(inactive),
            skipped_service_accounts=skipped_service_accounts,
            skipped_empty=skipped_empty,
            sample=inactive[:3],
        )

        result = {
            "inactive_users": inactive,
            "threshold_date": threshold.isoformat(),
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }
        span.ok(inactive_count=len(inactive), threshold_date=threshold.isoformat())
        return result


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
    with tracer.span("query_user_last_activity", user_email=user_email) as span:
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

        tracer.log(
            "query_user_last_activity",
            "executing BigQuery SQL",
            project_id=project_id,
            table=table,
            sql=sql.strip(),
        )

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
                bigquery.ScalarQueryParameter("email", "STRING", user_email),
            ]
        )

        client = _get_bq_client()
        job = client.query(sql, job_config=job_config)
        tracer.log("query_user_last_activity", "BigQuery job submitted", job_id=job.job_id)

        rows = list(job.result())
        tracer.log(
            "query_user_last_activity",
            "BigQuery job complete",
            job_id=job.job_id,
            row_count=len(rows),
        )

        if not rows:
            tracer.log(
                "query_user_last_activity",
                "no rows found — user never active",
                user_email=user_email,
            )
            result = {
                "user": user_email,
                "last_activity": "never",
                "last_method": None,
                "days_since_activity": None,
            }
            span.ok(last_activity="never")
            return result

        row = rows[0]
        last_act: date | None = row["last_activity"]
        last_method = row["last_method"]
        days_since = (date.today() - last_act).days if last_act else None

        tracer.log(
            "query_user_last_activity",
            "row found",
            last_activity=str(last_act),
            last_method=last_method,
            days_since_activity=days_since,
        )

        result = {
            "user": user_email,
            "last_activity": last_act.isoformat() if last_act else "never",
            "last_method": last_method,
            "days_since_activity": days_since,
        }
        span.ok(last_activity=result["last_activity"], days_since_activity=days_since)
        return result


def query_daily_usage(days_back: int = 30) -> dict[str, Any]:
    """
    Return a daily usage breakdown from the Discovery Engine / Gemini Enterprise
    analytics export in BigQuery (igbokwe.geminienterprise.analytics).

    Args:
        days_back: Number of past days to include in the report. Default 30.

    Returns:
        A dict with key "usage_records": list of
          {"date": str, "engine_id": str, "data_source": str,
           "daily_active_users": float|None, "search_count": float|None,
           "answer_count": float|None, "seats_purchased": float|None,
           "seats_claimed": float|None}
    """
    with tracer.span("query_daily_usage", days_back=days_back) as span:
        project_id = os.environ["GCP_PROJECT_ID"]
        since = date.today() - timedelta(days=days_back)
        analytics_table = f"`{project_id}.geminienterprise.analytics`"

        sql = f"""
            SELECT
                date,
                engine_id,
                data_source,
                SUM(daily_active_user_count)  AS daily_active_users,
                SUM(search_count)             AS search_count,
                SUM(answer_count)             AS answer_count,
                MAX(seats_purchased)          AS seats_purchased,
                MAX(seats_claimed)            AS seats_claimed
            FROM
                {analytics_table}
            WHERE
                date >= @since
                AND date IS NOT NULL
            GROUP BY
                1, 2, 3
            ORDER BY
                1 DESC, 2, 3
        """

        tracer.log(
            "query_daily_usage",
            "executing BigQuery SQL",
            project_id=project_id,
            analytics_table=analytics_table,
            since=since.isoformat(),
            sql=sql.strip(),
        )

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("since", "STRING", since.isoformat()),
            ]
        )

        client = _get_bq_client()
        job = client.query(sql, job_config=job_config)
        tracer.log("query_daily_usage", "BigQuery job submitted", job_id=job.job_id)

        rows = list(job.result())
        tracer.log(
            "query_daily_usage",
            "BigQuery job complete",
            job_id=job.job_id,
            row_count=len(rows),
        )

        records = [
            {
                "date": row["date"],
                "engine_id": row["engine_id"],
                "data_source": row["data_source"],
                "daily_active_users": row["daily_active_users"],
                "search_count": row["search_count"],
                "answer_count": row["answer_count"],
                "seats_purchased": row["seats_purchased"],
                "seats_claimed": row["seats_claimed"],
            }
            for row in rows
        ]

        tracer.log(
            "query_daily_usage",
            "records built",
            record_count=len(records),
            sample=records[:2],
        )
        span.ok(record_count=len(records))
        return {"usage_records": records}
