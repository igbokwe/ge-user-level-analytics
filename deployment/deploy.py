"""
Deploy the GE User Level Analytics agent to Vertex AI Agent Engine.

Usage:
    # Authenticate first (one-time)
    gcloud auth application-default login

    # Deploy (or re-deploy) the agent
    python deployment/deploy.py deploy

    # List deployed agents
    python deployment/deploy.py list

    # Delete a deployed agent
    python deployment/deploy.py delete --resource-name RESOURCE_NAME

    # Test the deployed agent with a prompt
    python deployment/deploy.py test --resource-name RESOURCE_NAME

Prerequisites:
    pip install -r requirements.txt
    Set all required environment variables (see .env / .env.example).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration (resolved after load_dotenv)
# ---------------------------------------------------------------------------

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "igbokwe")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get(
    "STAGING_BUCKET", f"gs://{PROJECT_ID}-agent-engine-staging"
)
DISPLAY_NAME = os.environ.get(
    "AGENT_ENGINE_DISPLAY_NAME", "ge-user-level-analytics-agent"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_staging_bucket() -> None:
    """Create the staging GCS bucket if it does not already exist."""
    bucket_name = STAGING_BUCKET.removeprefix("gs://")
    print(f"Ensuring staging bucket exists: {STAGING_BUCKET}")
    try:
        from google.cloud import storage  # noqa: PLC0415

        client = storage.Client(project=PROJECT_ID)
        bucket = client.lookup_bucket(bucket_name)
        if bucket is None:
            bucket = client.create_bucket(bucket_name, location=LOCATION)
            print(f"  Created bucket: gs://{bucket.name}")
        else:
            print(f"  Bucket already exists: gs://{bucket.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: Could not verify/create bucket via SDK ({exc}).")
        print(f"  Attempting via gcloud …")
        result = subprocess.run(
            [
                "gcloud",
                "storage",
                "buckets",
                "create",
                STAGING_BUCKET,
                "--project",
                PROJECT_ID,
                "--location",
                LOCATION,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            print(f"  gcloud error: {result.stderr.strip()}")
            sys.exit(1)
        print(f"  Bucket ready.")


def _init_vertexai() -> None:
    import vertexai  # noqa: PLC0415
    from google.cloud.aiplatform_v1beta1.services.reasoning_engine_service import (  # noqa: PLC0415
        ReasoningEngineServiceClient,
    )
    from google.cloud.aiplatform_v1beta1.services.reasoning_engine_service.transports import (  # noqa: PLC0415
        rest as rest_transport,
    )

    # Force REST transport on the Reasoning Engine client so that this script
    # works in environments where gRPC is blocked by a TLS-intercepting proxy.
    _original_init = ReasoningEngineServiceClient.__init__

    def _rest_init(self, *args, **kwargs):
        kwargs.setdefault("transport", "rest")
        _original_init(self, *args, **kwargs)

    ReasoningEngineServiceClient.__init__ = _rest_init

    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)


def _build_app():
    """Build the ADK AdkApp wrapper required by Agent Engine."""
    from vertexai.preview.reasoning_engines import AdkApp  # noqa: PLC0415

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent.agent import root_agent  # noqa: PLC0415

    env_vars = {
        "GCP_PROJECT_ID": PROJECT_ID,
        "GCP_LOCATION": LOCATION,
        "LOG_BUCKET": os.environ.get("LOG_BUCKET", "_Default"),
        "LOG_VIEW": os.environ.get("LOG_VIEW", "_AllLogs"),
        "INACTIVITY_THRESHOLD_DAYS": os.environ.get("INACTIVITY_THRESHOLD_DAYS", "45"),
        "WORKSPACE_DOMAIN": os.environ.get("WORKSPACE_DOMAIN", ""),
        "WORKSPACE_ADMIN_EMAIL": os.environ.get("WORKSPACE_ADMIN_EMAIL", ""),
        "GEMINI_ENTERPRISE_PRODUCT_ID": os.environ.get(
            "GEMINI_ENTERPRISE_PRODUCT_ID", "Google-Gemini-Enterprise"
        ),
        "GEMINI_ENTERPRISE_SKU_ID": os.environ.get("GEMINI_ENTERPRISE_SKU_ID", "1010310006"),
        "NOTIFICATION_SENDER_EMAIL": os.environ.get("NOTIFICATION_SENDER_EMAIL", ""),
        "ORG_ADMIN_EMAILS": os.environ.get("ORG_ADMIN_EMAILS", ""),
        # Use Gemini AI Studio API (generativelanguage.googleapis.com) instead of
        # Vertex AI publisher models, which are not enabled for this project.
        "GOOGLE_GENAI_USE_VERTEXAI": "0",
        "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEY", "AIzaSyBNsr7VGUviBPRQWlCzv7IYQigck9gbkpc"),
    }

    # Embed the OAuth client credentials so the agent can drive the Google
    # Identity for Agents consent flow at runtime.  The actual user tokens
    # are obtained on-demand via the ADK ToolContext OAuth flow — they are
    # never baked into the deployment.
    for oauth_var in ("OAUTH_CLIENT_ID", "OAUTH_CLIENT_SECRET"):
        value = os.environ.get(oauth_var, "")
        if value:
            env_vars[oauth_var] = value

    return AdkApp(agent=root_agent, env_vars=env_vars)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def deploy(args: argparse.Namespace) -> None:
    """Package and deploy the agent to Agent Engine."""
    _ensure_staging_bucket()
    _init_vertexai()

    from vertexai.preview import reasoning_engines  # noqa: PLC0415

    app = _build_app()

    print(f"\nDeploying '{DISPLAY_NAME}' to {PROJECT_ID} / {LOCATION} …")
    print("(This typically takes 3-5 minutes)\n")

    remote_app = reasoning_engines.ReasoningEngine.create(
        app,
        requirements=[
            "google-adk==1.26.0",
            "google-cloud-bigquery>=3.10.0",
            "google-api-python-client>=2.100.0",
            "google-auth>=2.20.0",
            "google-auth-httplib2>=0.2.0",
            "google-cloud-logging>=3.5.0",
            "google-cloud-storage>=2.10.0",
            "python-dotenv>=1.0.0",
            "pydantic>=2.0.0",
            "deprecated>=1.2.14",
        ],
        display_name=DISPLAY_NAME,
        description=(
            "Governs Gemini Enterprise licences: identifies inactive users (>45 days), "
            "revokes their licences, and notifies users and org administrators."
        ),
        extra_packages=["./agent"],
    )

    print("\nDeployment successful!")
    print(f"  Resource name : {remote_app.resource_name}")
    print(f"  Display name  : {DISPLAY_NAME}")
    print(f"\nTo run a test:")
    print(f"  python deployment/deploy.py test --resource-name {remote_app.resource_name}")

    # Persist resource name for convenience
    with open(".last_resource_name", "w") as f:
        f.write(remote_app.resource_name)
    print(f"\nResource name saved to .last_resource_name")


def list_agents(args: argparse.Namespace) -> None:
    """List all Agent Engine deployments in the project."""
    _init_vertexai()
    from vertexai.preview import reasoning_engines  # noqa: PLC0415

    agents = reasoning_engines.ReasoningEngine.list()
    if not agents:
        print("No Agent Engine deployments found.")
        return
    print(f"{'Resource Name':<70}  Display Name")
    print("-" * 100)
    for agent in agents:
        print(f"  {agent.resource_name:<68}  {agent.display_name}")


def delete(args: argparse.Namespace) -> None:
    """Delete a deployed agent by resource name."""
    _init_vertexai()
    from vertexai.preview import reasoning_engines  # noqa: PLC0415

    resource_name: str = args.resource_name
    print(f"Deleting agent: {resource_name} …")
    agent = reasoning_engines.ReasoningEngine(resource_name)
    agent.delete()
    print("Deleted.")


def _get_access_token() -> str:
    """Obtain a GCP access token via application default credentials."""
    import json  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    if not os.path.exists(adc_path):
        print("ERROR: No application default credentials found. Run: gcloud auth application-default login")
        sys.exit(1)
    with open(adc_path) as f:
        creds = json.load(f)
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())["access_token"]


def test_agent(args: argparse.Namespace) -> None:
    """Send a test prompt to a deployed agent and stream the response."""
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    # Resolve resource name from arg or saved file
    resource_name: str = args.resource_name
    if not resource_name and os.path.exists(".last_resource_name"):
        with open(".last_resource_name") as f:
            resource_name = f.read().strip()
        print(f"Using saved resource name: {resource_name}")

    if not resource_name:
        print("ERROR: --resource-name is required (or deploy first to save it automatically).")
        sys.exit(1)

    # Convert short ID to full resource name
    if not resource_name.startswith("projects/"):
        resource_name = (
            f"projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{resource_name}"
        )

    prompt: str = args.prompt
    print(f"Sending prompt to {resource_name} …")
    print(f"Prompt: {prompt}\n")
    print("=" * 60)

    token = _get_access_token()
    base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/{resource_name}"

    def _api(path: str, body: dict | None = None, method: str = "POST") -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{base_url}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            return json.loads(resp.read())
        except urllib.error.HTTPError as exc:  # noqa: BLE001
            body_text = exc.read().decode()
            print(f"API error {exc.code}: {body_text[:500]}")
            sys.exit(1)

    # Create session and wait for the LRO to complete
    lro = _api("/sessions", {"userId": "test-user"})
    lro_name = lro.get("name", "")
    for _ in range(20):
        time.sleep(2)
        lro_check_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1/{lro_name}"
        req = urllib.request.Request(lro_check_url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        if result.get("done"):
            break
    session_id = lro_name.split("/sessions/")[1].split("/")[0]
    print(f"Session: {session_id}\n")

    # Stream the response using raw REST (avoids gRPC TLS issues)
    stream_req = urllib.request.Request(
        f"{base_url}:streamQuery",
        data=json.dumps({
            "input": {
                "user_id": "test-user",
                "session_id": session_id,
                "message": prompt,
            }
        }).encode(),
        method="POST",
    )
    stream_req.add_header("Authorization", f"Bearer {token}")
    stream_req.add_header("Content-Type", "application/json")

    try:
        resp = urllib.request.urlopen(stream_req, timeout=120)
    except urllib.error.HTTPError as exc:  # noqa: BLE001
        print(f"Stream error {exc.code}: {exc.read().decode()[:500]}")
        sys.exit(1)

    # Parse NDJSON response chunks
    for raw_line in resp:
        line = raw_line.decode("utf-8").strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue

        content = chunk.get("content", {})
        role = content.get("role", "")
        parts = content.get("parts", [])

        for part in parts:
            # Text response from the model
            if "text" in part:
                print(part["text"], end="", flush=True)

            # Tool/function call from the model
            elif "function_call" in part:
                fc = part["function_call"]
                name = fc.get("name", "unknown")
                if name == "adk_request_credential":
                    # Extract and display the OAuth consent URL
                    auth_cfg = fc.get("args", {}).get("authConfig", {})
                    auth_uri = (
                        auth_cfg.get("exchangedAuthCredential", {})
                        .get("oauth2", {})
                        .get("authUri", "")
                    )
                    if auth_uri:
                        print("\n" + "=" * 60)
                        print("OAUTH CONSENT REQUIRED")
                        print("Open this URL in a browser to authorise the agent:")
                        print(f"\n  {auth_uri}\n")
                        print("=" * 60)
                else:
                    print(f"\n[tool call: {name}]", flush=True)

            # Tool result / function response
            elif "function_response" in part:
                fr = part["function_response"]
                name = fr.get("name", "unknown")
                if name != "adk_request_credential":
                    print(f"[tool result: {name}]", flush=True)

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the GE User Level Analytics agent on Vertex AI Agent Engine"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("deploy", help="Deploy (or re-deploy) the agent")
    sub.add_parser("list", help="List deployed agents in the project")

    del_p = sub.add_parser("delete", help="Delete a deployed agent")
    del_p.add_argument("--resource-name", required=True, help="Agent Engine resource name")

    test_p = sub.add_parser("test", help="Send a test prompt to a deployed agent")
    test_p.add_argument(
        "--resource-name",
        default="",
        help="Agent Engine resource name (omit to use .last_resource_name)",
    )
    test_p.add_argument(
        "--prompt",
        default=(
            "Run a DRY RUN of the revocation cycle using the 45-day inactivity threshold. "
            "Do not actually revoke any licences. Report how many inactive users you find."
        ),
        help="Prompt to send to the agent",
    )

    args = parser.parse_args()
    dispatch = {
        "deploy": deploy,
        "list": list_agents,
        "delete": delete,
        "test": test_agent,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
