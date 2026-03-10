"""
Deploy the GE User Level Analytics agent to Vertex AI Agent Engine.

Usage:
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
    Set all required environment variables (see .env.example).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

import vertexai
from dotenv import load_dotenv
from vertexai.preview import reasoning_engines

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ["STAGING_BUCKET"]
DISPLAY_NAME = os.environ.get(
    "AGENT_ENGINE_DISPLAY_NAME", "ge-user-level-analytics-agent"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_vertexai() -> None:
    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)


def _build_app():
    """Build the ADK AdkApp wrapper required by Agent Engine."""
    from google.adk.agents import Agent  # noqa: PLC0415  (local import keeps deploy independent)
    from google.adk.runners import AdkApp  # type: ignore[import-untyped]

    # Import the root agent (deferred to avoid loading env at module level)
    from agent.agent import root_agent  # noqa: PLC0415

    return AdkApp(agent=root_agent)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def deploy(args: argparse.Namespace) -> None:
    """Package and deploy the agent to Agent Engine."""
    _init_vertexai()
    app = _build_app()

    print(f"Deploying '{DISPLAY_NAME}' to {PROJECT_ID} / {LOCATION} …")

    remote_app = reasoning_engines.ReasoningEngine.create(
        app,
        requirements=[
            "google-adk==1.0.0",
            "google-cloud-bigquery==3.25.0",
            "google-api-python-client==2.140.0",
            "google-auth==2.34.0",
            "google-auth-httplib2==0.2.0",
            "google-cloud-logging==3.11.0",
            "python-dotenv==1.0.1",
            "pydantic==2.8.2",
        ],
        display_name=DISPLAY_NAME,
        description=(
            "Governs Gemini Enterprise licences: identifies inactive users, "
            "revokes their licences, and notifies users and admins."
        ),
        extra_packages=["./agent"],
    )

    print(f"\nDeployment successful!")
    print(f"  Resource name : {remote_app.resource_name}")
    print(f"  Display name  : {DISPLAY_NAME}")
    print(f"\nTo test the agent:")
    print(f"  python deployment/deploy.py test --resource-name {remote_app.resource_name}")


def list_agents(args: argparse.Namespace) -> None:
    """List all Agent Engine deployments in the project."""
    _init_vertexai()
    agents = reasoning_engines.ReasoningEngine.list()
    if not agents:
        print("No Agent Engine deployments found.")
        return
    for agent in agents:
        print(f"  {agent.resource_name}  ({agent.display_name})")


def delete(args: argparse.Namespace) -> None:
    """Delete a deployed agent by resource name."""
    _init_vertexai()
    resource_name: str = args.resource_name
    print(f"Deleting agent: {resource_name} …")
    agent = reasoning_engines.ReasoningEngine(resource_name)
    agent.delete()
    print("Deleted.")


def test_agent(args: argparse.Namespace) -> None:
    """Send a test prompt to a deployed agent."""
    _init_vertexai()
    resource_name: str = args.resource_name
    prompt: str = args.prompt

    print(f"Querying agent {resource_name} …\n")
    remote_app = reasoning_engines.ReasoningEngine(resource_name)
    session = remote_app.create_session()

    response = remote_app.stream_query(
        user_id="deploy-tester",
        session_id=session["id"],
        message=prompt,
    )
    for chunk in response:
        if chunk.get("type") == "text":
            print(chunk["text"], end="", flush=True)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the GE User Level Analytics agent on Vertex AI Agent Engine"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("deploy", help="Deploy (or re-deploy) the agent")
    sub.add_parser("list", help="List deployed agents")

    del_p = sub.add_parser("delete", help="Delete a deployed agent")
    del_p.add_argument("--resource-name", required=True, help="Agent Engine resource name")

    test_p = sub.add_parser("test", help="Send a test prompt to a deployed agent")
    test_p.add_argument("--resource-name", required=True, help="Agent Engine resource name")
    test_p.add_argument(
        "--prompt",
        default="Run a full revocation cycle using the default 45-day inactivity threshold.",
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
