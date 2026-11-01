"""Authenticated AgentArts deployment check, without a billable model call."""
from __future__ import annotations

import uuid
from typing import Callable

from .agentarts_runtime import RUNTIME_INFO_CONTRACT
from .config import Settings
from .project_models import ProjectModelError, request_http_subprocess
from .skills import load_catalog


def check_runtime(settings: Settings, *, expected_model: str | None = None,
                  transport: Callable | None = None) -> dict:
    """Require a reachable Runtime with the exact local Skill library version."""
    if not settings.agentarts_url or not settings.agentarts_key:
        raise ProjectModelError("not_configured", "AgentArts Runtime URL and API key are required")
    if not settings.agentarts_url.startswith("https://"):
        raise ProjectModelError("invalid_endpoint", "AgentArts Runtime URL must use HTTPS")
    send = transport or request_http_subprocess
    envelope = send(url=settings.agentarts_url,
                    headers={"Authorization": "Bearer " + settings.agentarts_key,
                             "X-Hw-Agentarts-Session-Id": "probe-" + uuid.uuid4().hex},
                    payload={"contract_version": RUNTIME_INFO_CONTRACT}, timeout_s=30.0)
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        code = envelope.get("code") if isinstance(envelope, dict) else None
        if code == "http_error" and type(envelope.get("status")) is int:
            raise ProjectModelError("http_error", f"AgentArts Runtime returned HTTP {envelope['status']}")
        raise ProjectModelError("runtime_unreachable", "AgentArts Runtime check failed; inspect endpoint, access and deployment")
    body = envelope.get("body")
    if not isinstance(body, dict) or body.get("contract_version") != RUNTIME_INFO_CONTRACT:
        raise ProjectModelError("invalid_contract", "Endpoint does not serve the TestFix Runtime info contract")
    if body.get("skill_catalog") != load_catalog().snapshot():
        raise ProjectModelError("catalog_mismatch", "AgentArts Runtime Skill catalog differs from local source")
    if body.get("ready") is not True or not isinstance(body.get("model"), str) or not body["model"]:
        raise ProjectModelError("upstream_unconfigured", "AgentArts Runtime has no configured upstream model")
    if expected_model is not None and body["model"] != expected_model:
        raise ProjectModelError("model_mismatch", "Requested model differs from the AgentArts Runtime model")
    if body.get("supported_contracts") != ["testfix.role.v1", "testfix.project-skill.v1"] or body.get("profiles") != ["flaskr_project", "python_package"]:
        raise ProjectModelError("invalid_contract", "AgentArts Runtime capability contract is incompatible")
    return {"ready": True, "contract_version": RUNTIME_INFO_CONTRACT,
            "upstream": body.get("upstream"), "model": body["model"],
            "skill_catalog_matches": True, "profiles": body["profiles"],
            "note": "Runtime responded with matching configuration; this check does not invoke the model or run tests"}
