"""
deploy_eqes_agent.py — Deploy the EQES Main Agent to Google Agent Runtime.

All configuration is read from:
  - config/agent_runtime_config.json  (static infra settings)
  - .env / environment variables       (secrets and environment-specific values)

Run from the project root:
    python -m deploy.deploy_eqes_agent
"""
import datetime
import json
import logging
import os
import sys

from vertexai import types

from common.config import cfg, agent_cfg, vertexai_client
from common.deploy_utils import (
    build_proxy_env_vars,
    validate_labels,
)

_DEPLOY_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_DEPLOY_DIR)

_AC = agent_cfg("eqes_orchestrator")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _as_rest_env_list(env_vars: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": key, "value": value} for key, value in env_vars.items()]


def _rest_v1_equivalent_payload(config: dict) -> dict:
    return {
        "displayName": config["display_name"],
        "description": config.get("description", ""),
        "labels": config.get("labels", {}),
        "encryptionSpec": {
            "kmsKeyName": config["encryption_spec"]["kms_key_name"],
        },
        "spec": {
            "agentFramework": config["agent_framework"],
            "identityType": str(config["identity_type"]),
            "containerSpec": {
                "imageUri": config["container_spec"]["image_uri"],
            },
            "deploymentSpec": {
                "minInstances": config["min_instances"],
                "maxInstances": config["max_instances"],
                "resourceLimits": config["resource_limits"],
                "containerConcurrency": config["container_concurrency"],
                "pscInterfaceConfig": config["psc_interface_config"],
                "env": _as_rest_env_list(config.get("env_vars", {})),
            },
        },
    }


def _build_deploy_config() -> dict:
    if not cfg.container_image:
        sys.exit("Missing required config value: container_image")

    container_spec = {
        "image_uri": cfg.container_image,
    }

    deployment_settings = {
        "min_instances": _AC.min_instances,
        "max_instances": _AC.max_instances,
        "resource_limits": {"cpu": _AC.cpu, "memory": _AC.memory},
        "container_concurrency": _AC.container_concurrency,
        "psc_interface_config": {
            "network_attachment": cfg.psc_network_attachment,
            "dns_peering_configs": cfg.dns_peering_configs,
        },
    }

    runtime_env_vars = {
        # GCP identity
        "GCP_PROJECT_ID": cfg.project_id,
        "GCP_LOCATION": cfg.location,
        # Logging level — driven by log_level in agent_runtime_config.json
        "LOG_LEVEL": cfg.log_level.upper(),
        # Agent Runtime tuning
        "NUM_WORKERS": str(_AC.num_workers),
        # Observability
        "OTEL_SERVICE_NAME": _AC.otel_service_name,
        "OTEL_RESOURCE_ATTRIBUTES": f"agent=eqes_orchestrator,project={cfg.project_id}",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        # LiteLLM / Vegas model
        "LITELLM_MODEL": os.environ["LITELLM_MODEL"],
        "LITELLM_PROXY_API_KEY": os.environ["LITELLM_PROXY_API_KEY"],
        "LITELLM_API_BASE": os.environ["LITELLM_API_BASE"],
        # Vegas direct tool
        "VEGAS_API_KEY": os.environ["VEGAS_API_KEY"],
        "VEGAS_HOST": os.environ.get("VEGAS_HOST", ""),
        "VEGAS_PROJECT": os.environ.get("VEGAS_PROJECT", ""),
        "VEGAS_LOCATION": os.environ.get("VEGAS_LOCATION", ""),
        "VEGAS_MODEL": os.environ.get("VEGAS_MODEL", ""),
        "VEGAS_MODEL_FALLBACKS": os.environ.get("VEGAS_MODEL_FALLBACKS", ""),
        "VEGAS_TIMEOUT": os.environ.get("VEGAS_TIMEOUT", "120.0"),
        "VEGAS_CONNECT_TIMEOUT": os.environ.get("VEGAS_CONNECT_TIMEOUT", "10.0"),
        "VEGAS_MAX_RETRIES": os.environ.get("VEGAS_MAX_RETRIES", "3"),
        # pyvegas — disable DB/A2A features not available in Agent Runtime
        "POSTGRES_LG_CHECKPOINTER_ENABLED": "false",
        "PYVEGAS_LOCAL_MODE": "true",
        "POSTGRES_LG_STORE_ENABLED": "false",
        "POSTGRES_A2A_STORE_ENABLED": "false",
        "ENVIRONMENT": os.environ.get("ENVIRONMENT", "test"),
        # Memory Bank — mode drives which strategy is used at runtime.
        # 'agent_runtime' uses built-in ADK memory (no pre-created resource needed).
        # 'decoupled'     requires MEMORY_BANK_RESOURCE_NAME (pre-created resource).
        # 'disabled'      no long-term memory.
        "MEMORY_BANK_MODE": cfg.memory_bank.mode,
        "MEMORY_BANK_IDLE_SECONDS": str(cfg.memory_bank.idle_trigger_seconds),
        "MEMORY_BANK_RESOURCE_NAME": os.environ.get("MEMORY_BANK_RESOURCE_NAME", ""),
        # NOTE: GOOGLE_CLOUD_AGENT_ENGINE_ID is injected automatically by Agent Runtime.
        # We do NOT set it here. See:
        # https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/setup
        # pip
        "PIP_NO_INPUT": "1",
        # Proxy
        **build_proxy_env_vars(),
    }

    # Declare all operations exposed by the BYOC container's main.py endpoints.
    # Required for BYOC/BYOD so Agent Runtime knows what methods the container
    # supports (it cannot introspect the container the way it can managed deployments).
    class_methods = [
        {"api_mode": "",             "name": "get_session"},
        {"api_mode": "",             "name": "list_sessions"},
        {"api_mode": "",             "name": "create_session"},
        {"api_mode": "",             "name": "delete_session"},
        {"api_mode": "async",        "name": "async_get_session"},
        {"api_mode": "async",        "name": "async_list_sessions"},
        {"api_mode": "async",        "name": "async_create_session"},
        {"api_mode": "async",        "name": "async_delete_session"},
        {"api_mode": "async",        "name": "async_add_session_to_memory"},
        {"api_mode": "async",        "name": "async_search_memory"},
        {"api_mode": "stream",       "name": "stream_query"},
        {"api_mode": "async_stream", "name": "async_stream_query"},
        {"api_mode": "async_stream", "name": "streaming_agent_run_with_events"},
    ]

    return {
        "display_name": _AC.display_name,
        "description": _AC.description,
        "container_spec": container_spec,
        **deployment_settings,
        "agent_framework": cfg.agent_framework,
        "labels": cfg.labels,
        "encryption_spec": {
            "kms_key_name": os.environ["GCP_KMS_KEY_NAME"],
        },
        "env_vars": runtime_env_vars,
        "class_methods": class_methods,
    }


def deploy() -> tuple[str, str]:
    """Create the EQES Main Agent on Agent Runtime. Returns (resource_name, endpoint)."""
    required_env = ["GCP_PROJECT_ID", "GCP_KMS_KEY_NAME", "LITELLM_MODEL",
                    "LITELLM_PROXY_API_KEY", "LITELLM_API_BASE", "VEGAS_API_KEY"]
    missing = [k for k in required_env if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing required environment variables: {missing}")

    validate_labels(cfg.labels)

    logger.info("%s", "=" * 60)
    logger.info("Deploying EQES Main Agent to Google Agent Runtime")
    logger.info("  Project  : %s", cfg.project_id)
    logger.info("  Location : %s", cfg.location)
    logger.info("%s", "=" * 60)

    os.chdir(_PROJECT_ROOT)
    client = vertexai_client()

    config = _build_deploy_config()
    config["identity_type"] = types.IdentityType.AGENT_IDENTITY
    rest_payload = _rest_v1_equivalent_payload(config)

    logger.info("  Creating agent (custom container image deployment)...")
    logger.info("  Container image: %s", config["container_spec"]["image_uri"])
    logger.info("  Resource limits: CPU=%s, Memory=%s",
                config["resource_limits"]["cpu"],
                config["resource_limits"]["memory"])
    logger.debug("  Full config: %s", json.dumps(config, indent=2, default=str))
    logger.debug("  REST v1-equivalent payload: %s", json.dumps(rest_payload, indent=2, default=str))
    remote_agent = client.agent_engines.create(config=config)

    resource_name = remote_agent.api_resource.name
    resource_id = resource_name.split("/")[-1]
    endpoint = cfg.endpoint_url(resource_id)

    logger.info("  Resource name : %s", resource_name)
    logger.info("  Resource ID   : %s", resource_id)
    logger.info("  API endpoint  : %s", endpoint)

    try:
        ops = remote_agent.operation_schemas()
        logger.info("  Operations    : %s", [o.get("name", str(o)) for o in (ops or [])])
    except Exception as exc:
        logger.debug("Failed to read operation schemas: %s", exc)

    # Write self-describing deployment record
    output_file = os.path.join(_DEPLOY_DIR, "eqes_agent_deployment_latest.json")
    with open(output_file, "w") as f:
        json.dump({
            "resource_name": resource_name,
            "resource_id": resource_id,
            "endpoint": endpoint,
            "agent": "eqes_orchestrator",
            "deployed_at": datetime.datetime.now().isoformat(),
        }, f, indent=2)
    logger.info("  Deployment record: %s", output_file)

    console_url = (
        f"https://console.cloud.google.com/vertex-ai/agents/agent-engines/"
        f"locations/{cfg.location}/agent-engines/{resource_id}"
        f"/playground?project={cfg.project_id}"
    )
    logger.info("  Console: %s", console_url)
    logger.info("  Next step — update client/.env: EQES_AGENT_RESOURCE_ID=%s", resource_id)

    return resource_name, endpoint


if __name__ == "__main__":
    deploy()
