#!/usr/bin/env python3
"""Deploy EQES Main Agent (Orchestrator) to Vertex AI Agent Runtime."""

import os
import json
import vertexai
from vertexai import agent_engines

# ── Hardcoded constants ────────────────────────────────────────────────────────
PROJECT_ID      = "vz-it-np-voev-dev-voevdo-0"
REGION          = "us-east4"
DISPLAY_NAME    = "eqes-main-agent"
SERVICE_ACCOUNT = "sa-dev-voev-app-voevdo-0@vz-it-np-voev-dev-voevdo-0.iam.gserviceaccount.com"
CMEK_KEY_PATH   = "projects/vz-it-np-d0sv-vsadkms-0/locations/us-east4/keyRings/vz-it-np-kr-corp/cryptoKeys/vz-it-np-kms-voev"
STAGING_BUCKET  = "gs://voev-dev-voevdo-0-usmr-warehouse"

# Set to existing resource name to UPDATE instead of create.
# Leave empty ("") to create a new agent.
AGENT_RESOURCE_NAME = "projects/1041813482705/locations/us-east4/reasoningEngines/7058204943345254400"

CONTAINER_ENV = {
    "CORP_PROXY_HOST":    "proxy.ebiz.verizon.com",
    "CORP_PROXY_PORT":    "80",
}


def get_requirements():
    return [
        "google-adk[gcp]==2.3.0",
        "opentelemetry-instrumentation-google-genai>=0.1.0,<1.0.0",
        "gcsfs>=2024.11.0",
        "google-cloud-logging>=3.12.0,<4.0.0",
        "google-cloud-aiplatform[agent-engines]>=1.156.0",
        "protobuf>=6.31.1,<7.0.0",
        "nest-asyncio>=1.6.0",
        "python-dotenv>=1.0.0",
        "requests>=2.32.0",
        "google-auth>=2.29.0",
    ]


def deploy():
    from app.agent_runtime_app import agent_runtime

    print("Initializing Vertex AI...")
    print(f"  Project: {PROJECT_ID}")
    print(f"  Region : {REGION}")
    print(f"  CMEK   : {CMEK_KEY_PATH}")
    print(f"  Bucket : {STAGING_BUCKET}")

    vertexai.init(project=PROJECT_ID, location=REGION, staging_bucket=STAGING_BUCKET)

    existing = AGENT_RESOURCE_NAME.strip()
    if existing:
        print(f"\nUpdating existing agent: {existing}")
        print("(This can take 5-10 minutes)")
        remote = agent_engines.get(existing)
        remote = remote.update(
            agent_engine=agent_runtime,
            requirements=get_requirements(),
            extra_packages=["./app"],
            display_name=DISPLAY_NAME,
            description="EQES Main Agent — Orchestrator that routes queries to specialized Verizon agents",
            env_vars=CONTAINER_ENV,
        )
        print("\nAgent updated successfully!")
    else:
        print(f"\nCreating new agent '{DISPLAY_NAME}'...")
        print("(This can take 5-10 minutes)")
        remote = agent_engines.create(
            agent_engine=agent_runtime,
            requirements=get_requirements(),
            extra_packages=["./app"],
            display_name=DISPLAY_NAME,
            description="EQES Main Agent — Orchestrator that routes queries to specialized Verizon agents",
            encryption_spec={"kms_key_name": CMEK_KEY_PATH},
            service_account=SERVICE_ACCOUNT,
            env_vars=CONTAINER_ENV,
        )
        print("\nAgent created successfully!")

    print(f"\n  Resource name: {remote.resource_name}")
    print(f"  Display name : {remote.display_name}")
    print("\n  Copy the resource name above into AGENT_RESOURCE_NAME for future updates.")

    with open("agent_runtime_metadata.json", "w") as f:
        json.dump({
            "resource_name": remote.resource_name,
            "display_name": remote.display_name,
            "project": PROJECT_ID,
            "region": REGION,
            "cmek_key": CMEK_KEY_PATH,
        }, f, indent=2)
    print("Metadata saved to agent_runtime_metadata.json")
    return remote


if __name__ == "__main__":
    os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
    deploy()
