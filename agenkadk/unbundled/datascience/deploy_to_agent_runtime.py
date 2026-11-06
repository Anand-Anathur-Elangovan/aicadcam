#!/usr/bin/env python3
"""Deploy Verizon Data Science Agent to Vertex AI Agent Runtime."""

import os
import json
import vertexai
from vertexai import agent_engines

# ── Hardcoded constants ────────────────────────────────────────────────────────
PROJECT_ID      = "vz-it-np-voev-dev-voevdo-0"
REGION          = "us-east4"
DISPLAY_NAME    = "verizon-data-agent"
SERVICE_ACCOUNT = "sa-dev-voev-app-voevdo-0@vz-it-np-voev-dev-voevdo-0.iam.gserviceaccount.com"
CMEK_KEY_PATH   = "projects/vz-it-np-d0sv-vsadkms-0/locations/us-east4/keyRings/vz-it-np-kr-corp/cryptoKeys/vz-it-np-kms-voev"
STAGING_BUCKET  = "gs://voev-dev-voevdo-0-usmr-warehouse"

# Set to existing resource name to UPDATE instead of create.
# Leave empty ("") to create a new agent.
AGENT_RESOURCE_NAME = "projects/1041813482705/locations/us-east4/reasoningEngines/8284732154361937920"

CONTAINER_ENV = {
    "SLACK_BOT_TOKEN":             os.environ.get("SLACK_BOT_TOKEN", ""),
    "BQ_PROJECT":                  PROJECT_ID,
    "EMAIL_SERVICE_URL":           "https://email-service-vdsi.ebiz.verizon.com",
    "SMTP_HOST":                   "vzsmtp.verizon.com",
    "SMTP_PORT":                   "25",
    "DEFAULT_FROM_EMAIL":          "do-not-reply@verizon.com",
    # Corporate proxy — required to reach internal Verizon services (AYS, email) from GCP.
    # Remove/change these once VPC connector is set up.
    "CORP_PROXY_HOST":             "proxy.ebiz.verizon.com",
    "CORP_PROXY_PORT":             "80",
    # AYS relay — email service /ays-proxy endpoint that forwards AYS calls via internal proxy
    # (Agent Runtime GCP managed VPC cannot resolve proxy.ebiz.verizon.com directly)
    "AYS_RELAY_URL":               "https://email-service-vdsi.ebiz.verizon.com/ays-proxy",
    # AYS incident API auth — POC only, rotate before production
    "CREATE_INCIDENT_BASIC_AUTH":  "_svc-VOE:+Ia(f#{8.agn!HfB3*7T$,B>((+oSAr4PnOoQ#yN",
    # AYS queue assignment group IDs
    "AYS_EUS_ASSIGNMENT_GROUP":    "1c181d9697a72d90c1f83ac11153af2d",
    "AYS_CITRIX_ASSIGNMENT_GROUP": "1c181d9697a72d90c1f83ac11153af2d",  # TODO: update with Citrix queue sys_id
    "CREATE_INCIDENT_CMDB_CI":     "69abcebddbbdb608ee90feb5ae9619c5",
}


def get_requirements():
    return [
        "google-adk[gcp]==2.3.0",
        "opentelemetry-instrumentation-google-genai>=0.1.0,<1.0.0",
        "gcsfs>=2024.11.0",
        "google-cloud-logging>=3.12.0,<4.0.0",
        "google-cloud-aiplatform[agent-engines,evaluation]>=1.156.0",
        "protobuf>=6.31.1,<7.0.0",
        "google-cloud-bigquery>=3.25.0",
        "google-cloud-run>=0.10.0",
        "google-cloud-scheduler>=2.13.0",
        "matplotlib>=3.9.0",
        "pandas>=2.2.0",
        "jinja2>=3.1.0",
        "requests>=2.32.0",
        "pytz>=2024.1",
        "db-dtypes>=1.3.0",
        "numpy>=1.26.0",
        "scipy>=1.13.0",
        "seaborn>=0.13.0",
        "plotly>=5.22.0",
        "kaleido>=0.2.1",
        "slack-sdk>=3.27.0",
        "python-dotenv>=1.0.0",
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
            description="Verizon Data Science Multi-Agent — BQ NL2SQL, Analytics, Reports",
            env_vars=CONTAINER_ENV,
        )
        print("\n✅ Agent updated successfully!")
    else:
        print(f"\nCreating new agent '{DISPLAY_NAME}'...")
        print("(This can take 5-10 minutes)")
        remote = agent_engines.create(
            agent_engine=agent_runtime,
            requirements=get_requirements(),
            extra_packages=["./app"],
            display_name=DISPLAY_NAME,
            description="Verizon Data Science Multi-Agent — BQ NL2SQL, Analytics, Reports",
            encryption_spec={"kms_key_name": CMEK_KEY_PATH},
            service_account=SERVICE_ACCOUNT,
            env_vars=CONTAINER_ENV,
        )
        print("\n✅ Agent created successfully!")

    print(f"\n  Resource name: {remote.resource_name}")
    print(f"  Display name : {remote.display_name}")
    print("\n⚠️  Copy the resource name above into AGENT_RESOURCE_NAME for future updates.")

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
