"""
deploy_utils.py — Reusable helpers for Agent Runtime deployments.

Keeps deploy scripts thin: all shared logic lives here.
"""
from __future__ import annotations

import importlib
import os
import re
import sys

from vertexai._genai import _agent_engines_utils


def _merge_no_proxy_hosts(raw_no_proxy: str, required_hosts: list[str]) -> str:
    hosts = [host.strip() for host in raw_no_proxy.split(",") if host.strip()]
    for required in required_hosts:
        if required not in hosts:
            hosts.append(required)
    return ",".join(hosts)


def _parse_hosts(raw_hosts: str) -> list[str]:
    return [host.strip() for host in raw_hosts.split(",") if host.strip()]


def load_agent_instance(entrypoint_module: str, entrypoint_object: str):
    """Import and return the agent entrypoint object."""
    module = importlib.import_module(entrypoint_module)
    return getattr(module, entrypoint_object)


def generate_class_methods(agent_instance) -> list:
    """Derive the class_methods spec from the agent's register_operations()."""
    registered = _agent_engines_utils._get_registered_operations(agent=agent_instance)
    spec = _agent_engines_utils._generate_class_methods_spec_or_raise(
        agent=agent_instance, operations=registered
    )
    return [_agent_engines_utils._to_dict(m) for m in spec]


def build_proxy_env_vars() -> dict:
    """Return proxy env-var dict read entirely from the environment (no hardcoding).

    Expected .env / environment variables:
        PROXY_SERVER_URL   e.g. http://swp.agentengine.internal:8888
        NO_PROXY           comma-separated no-proxy hosts
        REQUIRED_NO_PROXY_HOSTS  comma-separated hosts that must always bypass proxy
    """
    proxy = os.environ.get("PROXY_SERVER_URL", "")
    no_proxy_raw = os.environ.get("NO_PROXY", "")
    required_no_proxy_raw = os.environ.get("REQUIRED_NO_PROXY_HOSTS", "")
    no_proxy = _merge_no_proxy_hosts(no_proxy_raw, _parse_hosts(required_no_proxy_raw))
    if not proxy:
        return {}
    return {
        "PROXY_SERVER_URL": proxy,
        "PYVEGAS_PROXY_URL": proxy,
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


_LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_labels(labels: dict[str, str]) -> None:
    """Validate labels against Agent Runtime constraints.

    Rules: max 64 labels, keys/values must match ^[a-z0-9][a-z0-9_-]*$,
    keys < 64 chars, values < 64 chars (both < 128 bytes UTF-8).
    Calls sys.exit with a clear message on any violation.
    """
    if len(labels) > 64:
        sys.exit("Invalid labels: no more than 64 labels are allowed.")

    errors: list[str] = []
    for key, value in labels.items():
        key_str, value_str = str(key), str(value)
        if len(key_str) >= 64 or len(key_str.encode("utf-8")) >= 128:
            errors.append(f"key '{key_str}': key too long (max 63 chars)")
        if len(value_str) >= 64 or len(value_str.encode("utf-8")) >= 128:
            errors.append(f"key '{key_str}': value too long (max 63 chars)")
        if not _LABEL_PATTERN.fullmatch(key_str):
            errors.append(f"key '{key_str}': must match ^[a-z0-9][a-z0-9_-]*$")
        if value_str and not _LABEL_PATTERN.fullmatch(value_str):
            errors.append(f"value for '{key_str}' ('{value_str}'): must match ^[a-z0-9][a-z0-9_-]*$")

    if errors:
        sys.exit("Invalid labels in config/agent_runtime_config.json:\n  - " + "\n  - ".join(errors))


def validate_requirements_file(project_root: str, filename: str) -> str:
    """Verify the requirements file exists; exit with a clear message otherwise."""
    path = os.path.join(project_root, filename)
    if not os.path.isfile(path):
        sys.exit(f"Requirements file not found: {path}")
    return filename
