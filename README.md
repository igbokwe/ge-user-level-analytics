# GE User Level Analytics — Gemini Enterprise Licence Governance Agent

An autonomous **Google ADK agent** deployed to **Vertex AI Agent Engine** that:

1. Queries Cloud Log Analytics to identify Gemini Enterprise users inactive for >45 days
2. Revokes their Gemini Enterprise licence via the Workspace Licensing API
3. Emails each revoked user with an explanation
4. Emails all org administrators a consolidated report
5. Writes a structured audit trail to Cloud Logging

---

## Architecture

```
ge-user-level-analytics/
├── agent/
│   ├── agent.py                  # ADK Agent definition (root_agent)
│   └── tools/
│       ├── log_analytics.py      # BigQuery Log Analytics queries
│       ├── license_manager.py    # Workspace Licensing API (revoke)
│       ├── notifier.py           # Gmail API (user + admin emails)
│       └── audit_logger.py       # Cloud Logging audit trail
├── deployment/
│   └── deploy.py                 # Deploy / manage Agent Engine
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | |
| GCP project | With billing enabled |
| Cloud Discovery Engine API | Audit logs enabled (Admin Read, Data Read, Data Write) |
| Log Analytics | Enabled on the `_Default` log bucket |
| Google Workspace | Admin SDK + Licensing API access |
| Service account | Domain-wide delegation; see required scopes below |
| Staging GCS bucket | For Agent Engine packaging |

### Required service-account OAuth scopes

```
https://www.googleapis.com/auth/cloud-platform
https://www.googleapis.com/auth/apps.licensing
https://www.googleapis.com/auth/admin.directory.user.readonly
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/logging.write
```

---

## Setup

```bash
# 1. Clone & install
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your project values

# 3. Enable required APIs
gcloud services enable \
  discoveryengine.googleapis.com \
  logging.googleapis.com \
  admin.googleapis.com \
  gmail.googleapis.com \
  licensing.googleapis.com \
  aiplatform.googleapis.com

# 4. Enable audit logs
#    IAM & Admin → Audit Logs → Cloud Discovery Engine API
#    Enable: Admin Read, Data Read, Data Write

# 5. Enable Log Analytics on the _Default bucket
#    Cloud Logging → Log Storage → _Default → Upgrade to Log Analytics
```

---

## Run locally

```bash
# Run the ADK development UI (interact with the agent in a browser)
adk web

# Or run a single turn from the CLI
adk run agent --message "Run a full revocation cycle."

# Dry-run (no actual licence revocations)
adk run agent --message "Run a dry-run revocation cycle. Do not actually revoke any licences."
```

---

## Deploy to Agent Engine

```bash
# Deploy
python deployment/deploy.py deploy

# List deployed agents
python deployment/deploy.py list

# Test a deployed agent
python deployment/deploy.py test \
  --resource-name projects/PROJECT_ID/locations/us-central1/reasoningEngines/ENGINE_ID

# Delete
python deployment/deploy.py delete \
  --resource-name projects/PROJECT_ID/locations/us-central1/reasoningEngines/ENGINE_ID
```

---

## Example agent prompts

| Intent | Prompt |
|---|---|
| Full revocation cycle | `"Run a full revocation cycle using the 45-day inactivity threshold."` |
| Dry run | `"Run a dry-run revocation cycle. Do not revoke any licences."` |
| Check a user | `"What is the last activity date for alice@example.com?"` |
| List licensed users | `"List all users currently holding a Gemini Enterprise licence."` |
| Usage report | `"Show me daily usage for the last 30 days."` |
| Custom threshold | `"Find users inactive for more than 60 days."` |

---

## Key SQL queries (Log Analytics)

### All inactive users (>45 days)
```sql
SELECT
    proto_payload.audit_log.authentication_info.principal_email AS user,
    MAX(DATE(timestamp)) AS last_activity
FROM `[project-id].global._Default._AllLogs`
WHERE proto_payload.audit_log.service_name = 'discoveryengine.googleapis.com'
GROUP BY 1
HAVING MAX(DATE(timestamp)) < DATE_SUB(CURRENT_DATE(), INTERVAL 45 DAY)
```

### Daily usage per user
```sql
SELECT
    DATE(timestamp) AS date,
    proto_payload.audit_log.authentication_info.principal_email AS user,
    proto_payload.audit_log.method_name AS method,
    COUNT(1) AS activity_count
FROM `[project-id].global._Default._AllLogs`
WHERE proto_payload.audit_log.service_name = 'discoveryengine.googleapis.com'
GROUP BY 1, 2, 3
ORDER BY 1 DESC
```

---

## Audit trail

All revocations are logged to Cloud Logging under the log name:
`gemini-enterprise-revocation-audit`

Query in Cloud Logging:
```
logName="projects/PROJECT_ID/logs/gemini-enterprise-revocation-audit"
```

---

## Limitations

- Audit log ingestion can have a delay of several minutes; very recent activity
  may not yet appear in Log Analytics.
- Only covers interactions with `discoveryengine.googleapis.com`. Users accessing
  Gemini via other APIs will appear inactive even if they are active.
- Email notifications require the Gmail API and domain-wide delegation; ensure
  the sender address is a valid mailbox in your domain.
