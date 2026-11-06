from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=False)
except ImportError:
    pass

# Config file is always relative to this file's package root
_CONFIG_PATH = Path(__file__).parent.parent / "config" / "agent_runtime_config.json"


def _load_json() -> dict:
    if not _CONFIG_PATH.is_file():
        logging.getLogger(__name__).warning(
            "Config file not found at '%s'; using runtime-safe defaults.",
            _CONFIG_PATH,
        )
        return {}

    with open(_CONFIG_PATH) as f:
        return json.load(f)


@dataclass
class AgentConfig:
    """Per-agent deployment settings."""
    key: str
    display_name: str
    description: str
    min_instances: int
    max_instances: int
    otel_service_name: str
    entrypoint_module: str
    entrypoint_object: str
    cpu: str = "4"
    memory: str = "8Gi"
    container_concurrency: int = 9
    num_workers: int = 1


@dataclass
class MemoryBankConfig:
    """
    Memory Bank strategy configuration.

    mode:
      'disabled'      — no long-term memory (default).
      'decoupled'     — separate Memory Bank resource managed outside Agent Runtime
                        (requires MEMORY_BANK_RESOURCE_NAME env var and a pre-created
                        resource via deploy/create_memory_bank.py).
      'agent_runtime' — built-in Memory Bank managed by Agent Runtime / AdkApp
                        (requires AGENT_ENGINE_RESOURCE_NAME or the runtime sets it;
                        no pre-created resource needed).
    """
    mode: str = "disabled"           # disabled | decoupled | agent_runtime
    idle_trigger_seconds: int = 300  # idle-duration trigger for memory consolidation
    top_k: int = 5                   # max memories to retrieve per query


@dataclass
class DeployConfig:
    """Top-level deployment configuration (JSON + env overrides."""
    project_id: str
    project_number: str
    location: str
    agent_framework: str
    identity_type: str
    api_version: str
    psc_network_attachment: str
    container_image: str = ""
    log_level: str = "INFO"
    labels: dict[str, str] = field(default_factory=dict)
    dns_peering_configs: list = field(default_factory=list)
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    memory_bank: MemoryBankConfig = field(default_factory=MemoryBankConfig)

    def resource_name(self, resource_id: str) -> str:
        return (
            f"projects/{self.project_id}/locations/{self.location}"
            f"/reasoningEngines/{resource_id}"
        )

    def endpoint_url(self, resource_id: str) -> str:
        return (
            f"https://{self.location}-aiplatform.googleapis.com/{self.api_version}/"
            f"projects/{self.project_id}/locations/{self.location}/"
            f"reasoningEngines/{resource_id}"
        )


def _build_config() -> DeployConfig:
    raw: dict[str, Any] = _load_json()

    agents = {
        key: AgentConfig(
            key=key,
            display_name=val["display_name"],
            description=val["description"],
            min_instances=int(val["min_instances"]),
            max_instances=int(val["max_instances"]),
            otel_service_name=val["otel_service_name"],
            entrypoint_module=val["entrypoint_module"],
            entrypoint_object=val["entrypoint_object"],
            cpu=val.get("cpu", "1"),
            memory=val.get("memory", "2Gi"),
            container_concurrency=int(val.get("container_concurrency", 2)),
            num_workers=int(val.get("num_workers", 2)),
        )
        for key, val in raw.get("agents", {}).items()
    }

    mb_raw = raw.get("memory_bank", {})
    # env var MEMORY_BANK_MODE overrides config file (useful in CI / local dev)
    mb_mode = (
        os.environ.get("MEMORY_BANK_MODE", "").strip().lower()
        or mb_raw.get("mode", "disabled")
    )
    memory_bank = MemoryBankConfig(
        mode=mb_mode,
        idle_trigger_seconds=int(mb_raw.get("idle_trigger_seconds", 300)),
        top_k=int(mb_raw.get("top_k", 5)),
    )

    return DeployConfig(
        project_id=os.environ.get("GCP_PROJECT_ID", ""),
        project_number=os.environ.get("GCP_PROJECT_NUMBER", ""),
        location=raw.get("location", os.environ.get("GCP_LOCATION", "us-east4")),
        agent_framework=raw.get("agent_framework", "google-adk"),
        identity_type=raw.get("identity_type", "AGENT_IDENTITY"),
        api_version=raw.get("api_version", os.environ.get("API_VERSION", "v1")),
        container_image=raw.get("container_image", ""),
        log_level=raw.get("log_level", os.environ.get("LOG_LEVEL", "INFO")),
        labels=raw.get("labels", {}),
        psc_network_attachment=raw.get("psc_network_attachment", ""),
        dns_peering_configs=raw.get("dns_peering_configs", []),
        agents=agents,
        memory_bank=memory_bank,
    )


# Module-level singleton
cfg: DeployConfig = _build_config()


def agent_cfg(key: str) -> AgentConfig:
    """Return per-agent config by key (e.g. 'research')."""
    try:
        return cfg.agents[key]
    except KeyError:
        raise KeyError(
            f"No agent config for key '{key}'. "
            f"Available: {list(cfg.agents.keys())}"
        )


def vertexai_client():
    """Return a Vertex AI client pre-configured for this project/location.

    Respects REQUESTS_CA_BUNDLE / SSL_CERT_FILE for corporate CA bundles.
    Set PYTHONHTTPSVERIFY=0 to disable TLS verification in dev/test only.
    """
    import httpx
    import vertexai

    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca_bundle and not os.path.isfile(ca_bundle):
        import logging as _log
        _log.warning(
            f"[vertexai_client] CA bundle not found at '{ca_bundle}', "
            "falling back to system trust store."
        )
        ca_bundle = None

    if os.environ.get("PYTHONHTTPSVERIFY") == "0":
        verify: bool | str = False
    elif ca_bundle:
        verify = ca_bundle
    else:
        verify = True

    return vertexai.Client(
        project=cfg.project_id,
        location=cfg.location,
        http_options={
            "api_version": cfg.api_version,
            "httpx_client": httpx.Client(verify=verify),
        },
    )
