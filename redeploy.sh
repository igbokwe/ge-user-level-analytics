#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# redeploy.sh — Unregister old agent, delete old engine, deploy updated
#               agent (with debug tracing), register in Gemini app with
#               ALL_USERS access, then verify the registration.
#
# Usage:
#   chmod +x redeploy.sh
#   ./redeploy.sh
#
# Prerequisites:
#   gcloud auth application-default login
#   All required env vars set in .env (see .env.example)
# ---------------------------------------------------------------------------
set -euo pipefail

# ── Load .env ───────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
  set -o allexport
  source .env
  set +o allexport
fi

PROJECT_ID="${GCP_PROJECT_ID:-igbokwe}"
LOCATION="${GCP_LOCATION:-us-central1}"
ENGINE_ID="${GE_ENGINE_ID:-outcome-devtest_1772673946815}"

echo "================================================================"
echo "  GE User Level Analytics — Full Redeploy"
echo "  Project    : $PROJECT_ID"
echo "  Location   : $LOCATION"
echo "  GE Engine  : $ENGINE_ID"
echo "================================================================"
echo ""

# ── Step 1: Unregister all existing agents from the Gemini app ───────────────
echo "[1/5] Unregistering existing agents from Gemini app ($ENGINE_ID) …"

AGENTS_JSON=$(python3 - <<'PYEOF'
import sys, json, os
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import google.auth, google.auth.transport.requests, requests

ENGINE_ID = os.environ.get("GE_ENGINE_ID", "outcome-devtest_1772673946815")
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "igbokwe")
LOCATION = "us"
BASE_URL = f"https://{LOCATION}-discoveryengine.googleapis.com/v1alpha"
ASSISTANT_ID = "default_assistant"

creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(google.auth.transport.requests.Request())
headers = {
    "Authorization": f"Bearer {creds.token}",
    "x-goog-user-project": PROJECT_ID,
    "Content-Type": "application/json",
}
parent = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/collections/default_collection"
    f"/engines/{ENGINE_ID}/assistants/{ASSISTANT_ID}"
)
url = f"{BASE_URL}/{parent}/agents"
resp = requests.get(url, headers=headers)
resp.raise_for_status()
agents = resp.json().get("agents", [])
print(json.dumps(agents))
PYEOF
)

AGENT_COUNT=$(echo "$AGENTS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
echo "   Found $AGENT_COUNT existing agent(s)."

if [[ "$AGENT_COUNT" -gt 0 ]]; then
  python3 - <<PYEOF
import sys, json, os
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import google.auth, google.auth.transport.requests, requests

ENGINE_ID = os.environ.get("GE_ENGINE_ID", "outcome-devtest_1772673946815")
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "igbokwe")
LOCATION = "us"
BASE_URL = f"https://{LOCATION}-discoveryengine.googleapis.com/v1alpha"
ASSISTANT_ID = "default_assistant"

creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(google.auth.transport.requests.Request())
headers = {
    "Authorization": f"Bearer {creds.token}",
    "x-goog-user-project": PROJECT_ID,
    "Content-Type": "application/json",
}
parent = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/collections/default_collection"
    f"/engines/{ENGINE_ID}/assistants/{ASSISTANT_ID}"
)
url = f"{BASE_URL}/{parent}/agents"
resp = requests.get(url, headers=headers)
resp.raise_for_status()
agents = resp.json().get("agents", [])

for a in agents:
    agent_name = a["name"]
    agent_id = agent_name.split("/")[-1]
    del_url = f"{BASE_URL}/{agent_name}"
    del_resp = requests.delete(del_url, headers=headers)
    del_resp.raise_for_status()
    print(f"   Deleted agent: {agent_id} ({a.get('displayName','')})")
PYEOF
fi
echo "   Done."

# ── Step 2: Delete existing Agent Engine deployments ────────────────────────
echo ""
echo "[2/5] Deleting existing Agent Engine deployments …"

python3 - <<PYEOF
import sys, os
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

import vertexai
from vertexai.preview import reasoning_engines

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "igbokwe")
LOCATION   = os.environ.get("GCP_LOCATION", "us-central1")
STAGING    = os.environ.get("STAGING_BUCKET", f"gs://{PROJECT_ID}-agent-engine-staging")
DISPLAY    = os.environ.get("AGENT_ENGINE_DISPLAY_NAME", "ge-user-level-analytics-agent")

vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING)
agents = reasoning_engines.ReasoningEngine.list()
deleted = 0
for a in agents:
    if a.display_name == DISPLAY:
        print(f"   Deleting: {a.resource_name}")
        a.delete()
        deleted += 1
if deleted == 0:
    print("   No matching deployments found.")
else:
    print(f"   Deleted {deleted} deployment(s).")
PYEOF
echo "   Done."

# ── Step 3: Deploy updated agent to Agent Engine ─────────────────────────────
echo ""
echo "[3/5] Deploying updated agent to Vertex AI Agent Engine …"
echo "   (This typically takes 3-5 minutes)"
python3 deployment/deploy.py deploy
echo "   Done."

# ── Step 4: Register in Gemini app with ALL_USERS access ────────────────────
echo ""
echo "[4/5] Registering agent in Gemini app (ALL_USERS access) …"
python3 deployment/register_ge_app.py --engine-id "$ENGINE_ID" register
echo "   Done."

# ── Step 5: Verify registration ──────────────────────────────────────────────
echo ""
echo "[5/5] Verifying registration …"
python3 deployment/register_ge_app.py --engine-id "$ENGINE_ID" list

echo ""
echo "================================================================"
echo "  Redeploy complete!"
echo "  All users have access via Gemini app: $ENGINE_ID"
echo ""
echo "  Next steps:"
echo "    Test the agent:"
echo "      python3 deployment/deploy.py test"
echo ""
echo "    View trace logs in Cloud Logging:"
echo "      Log name : gemini-enterprise-agent-trace"
echo "      Project  : $PROJECT_ID"
echo "      Filter   : logName=~\"gemini-enterprise-agent-trace\""
echo "================================================================"
