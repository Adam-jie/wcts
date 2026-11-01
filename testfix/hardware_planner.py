"""Hardware-specific Skill planning over the existing bounded model transports.

The software nine-capability prompt is never used here. Only action descriptions
and frozen response contracts enter the model snapshot, without wire commands,
addresses, HTTP credentials, serial ports or SSH key paths.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator

from .config import DEFAULT_CODEX_MODEL, Settings
from .hardware import (Action, Device, HardwareRunRequest, Requirement, Strict,
                       _functional_assertion, _redact, safe_hardware_router)
from .project_models import ProjectModelError, ProjectModelProvider, _validate_schema
from .reports import now

SKILL_PATH = Path(__file__).parent / "hardware_skills/hardware-functional-testing/SKILL.md"
CONTRACT = "testfix.hardware-plan.v1"
_PLAN_LOCK = threading.Lock()


class HardwarePlanRequest(Strict):
    device: Device
    requirements: list[Requirement] = Field(min_length=1, max_length=32)
    allowed_actions: list[Action] = Field(min_length=1, max_length=32)
    provider: Literal["codex_cli", "openai", "ollama"] = "codex_cli"
    model: str = Field(default=DEFAULT_CODEX_MODEL, min_length=1, max_length=128)
    timeout_s: float = Field(default=120, ge=5, le=180)
    max_duration_s: float = Field(default=60, ge=1, le=120)

    @model_validator(mode="after")
    def declared_protocol(self):
        requirements = {item.id: item for item in self.requirements}
        if len(requirements) != len(self.requirements) or len({item.id for item in self.allowed_actions}) != len(self.allowed_actions):
            raise ValueError("需求和动作 ID 不能重复")
        for action in self.allowed_actions:
            if action.kind != self.device.kind or action.function_id not in requirements:
                raise ValueError("动作必须绑定所选连接协议和已声明的功能需求")
        for requirement in self.requirements:
            actions = [item for item in self.allowed_actions if item.function_id == requirement.id]
            if not actions or requirement.category == "functional" and not any(
                    _functional_assertion(item, assertion) for item in actions for assertion in item.assertions):
                raise ValueError(f"需求 {requirement.id} 缺少设备协议动作或行为预期；请提供命令/接口及预期回包")
        return self


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _binding(device, requirements, actions, plan) -> str:
    # Validate serialized defaults too (e.g. timeout 3 and 3.0 must bind alike).
    normalized = HardwareRunRequest.model_validate({"device": device.model_dump(),
        "requirements": [item.model_dump() for item in requirements],
        "allowed_actions": [item.model_dump() for item in actions], "plan": plan.model_dump()})
    return hashlib.sha256(_canonical({"device": normalized.device.model_dump(),
        "requirements": [item.model_dump() for item in normalized.requirements],
        "allowed_actions": [item.model_dump() for item in normalized.allowed_actions],
        "plan": normalized.plan.model_dump()}).encode()).hexdigest()


def _directory(settings: Settings, identifier: str, *, create=False) -> Path:
    root = settings.root.resolve()
    path = root / "artifacts/hardware-plans" / identifier
    for item in (path, *path.parents):
        if item.is_symlink() or hasattr(item, "is_junction") and item.is_junction():
            raise ValueError("Planning artifacts cannot use linked directories")
        if item == root:
            break
    if create:
        path.mkdir(parents=True, exist_ok=False)
    return path


def planning_provenance(settings: Settings, request: HardwareRunRequest) -> dict:
    manual = {"model_used": False, "planning_source": "manual_or_edited" if request.planning_id else "manual"}
    if not request.planning_id:
        return manual
    directory = _directory(settings, request.planning_id)
    path = directory / "planning.json"
    if path.is_symlink() or not path.is_file():
        return manual
    try:
        if path.stat().st_size > 200_000:
            return manual
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (receipt.get("schema") != CONTRACT or receipt.get("status") != "ready"
                or receipt.get("planning_id") != request.planning_id or receipt.get("model_used") is not True
                or receipt.get("binding_sha256") != _binding(request.device, request.requirements, request.allowed_actions, request.plan)):
            return manual
        return {"model_used": True, "planning_source": "llm", "planning_id": request.planning_id,
                "model_requested": receipt.get("model_requested"), "model_actual": receipt.get("model_actual")}
    except (OSError, ValueError, TypeError):
        return manual


def _schema(request: HardwarePlanRequest) -> dict:
    return {"type": "object", "additionalProperties": False,
        "required": ["summary", "steps", "uncovered"], "properties": {
            "summary": {"type": "string", "maxLength": 2000},
            "steps": {"type": "array", "maxItems": 32, "items": {"type": "object", "additionalProperties": False,
                "required": ["action_id", "purpose"], "properties": {
                    "action_id": {"type": "string", "enum": [item.id for item in request.allowed_actions]},
                    "purpose": {"type": "string", "minLength": 1, "maxLength": 1000}}}},
            "uncovered": {"type": "array", "maxItems": 32, "items": {"type": "object", "additionalProperties": False,
                "required": ["requirement_id", "reason"], "properties": {
                    "requirement_id": {"type": "string", "enum": [item.id for item in request.requirements]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 1000}}}}}}


def generate_hardware_plan(settings: Settings, request: HardwarePlanRequest, *, transport=None) -> dict:
    request = HardwarePlanRequest.model_validate(request.model_dump())
    skill = SKILL_PATH.read_bytes()
    skill_sha = hashlib.sha256(skill).hexdigest()
    snapshot = {"device_kind": request.device.kind,
        "requirements": [item.model_dump() for item in request.requirements],
        "actions": [{"id": item.id, "function_id": item.function_id, "description": item.description,
                     "response_contract": [assertion.model_dump() for assertion in item.assertions]}
                    for item in request.allowed_actions]}
    public = _canonical(snapshot)
    if len(public.encode()) > 96_000:
        raise ValueError("协议描述超过规划上下文预算，请拆分测试")
    secrets = []
    for owner in [request.device, *request.allowed_actions]:
        for value in getattr(owner, "headers", {}).values():
            if value:
                secrets.append(value)
                if value.lower().startswith("bearer "):
                    secrets.append(value[7:])
    secrets.extend(value for value in (getattr(request.device, "private_key_path", None),
                                      getattr(request.device, "known_hosts_path", None)) if value)
    secrets.extend(value for value in (settings.api_key, settings.agentarts_key) if value)
    if any(secret in text for text in _strings(snapshot) for secret in secrets):
        raise ValueError("功能描述或预期回包含设备凭据/本地密钥路径，请移除后生成计划")
    schema = _schema(request)
    instructions = (skill.decode("utf-8") + "\nAim for at most 4096 output tokens. Return one JSON object matching this schema. "
        "Use Chinese step purposes when requirements are Chinese. Do not execute or claim success.\n" + _canonical(schema))
    identifier = uuid.uuid4().hex
    directory = _directory(settings, identifier, create=True)
    receipt = {"schema": CONTRACT, "planning_id": identifier, "created_at": now(), "status": "running",
        "model_used": False, "model_requested": request.model, "model_actual": None,
        "skill_id": "hardware-functional-testing", "skill_sha256": skill_sha,
        "prompt_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
        "snapshot_sha256": hashlib.sha256(public.encode()).hexdigest(),
        "provider": request.provider, "device_kind": request.device.kind,
        "token_cap": 4096, "token_cap_enforcement": "prompt_only" if request.provider == "codex_cli" else "provider_parameter"}
    started = time.monotonic()
    try:
        # Reuse transport implementation, not software invoke()/SkillCatalog prompts.
        transport = transport or ProjectModelProvider(request.provider, settings, model=request.model,
            timeout_s=request.timeout_s, token_cap=4096, profile="python_package", artifact_dir=directory / "transport")
        if transport._scrub(public) != public:
            raise ValueError("协议描述包含凭据，请移除凭据后生成计划")
        (directory / "request.json").write_text(json.dumps({"instructions": instructions,
            "snapshot": snapshot, "output_schema": schema}, ensure_ascii=False, indent=2), encoding="utf-8")
        receipt["model_used"] = True
        if request.provider == "codex_cli":
            result, _raw, actual, evidence, usage = transport._invoke_cli(instructions, public, schema, request.timeout_s)
        else:
            result, _raw, actual, usage = transport._invoke_http("hardware-functional-testing", instructions,
                snapshot, schema, request.timeout_s, 4096)
            evidence = "service_response" if actual else None
        receipt.update(model_actual=actual, model_evidence=evidence, usage=usage)
        (directory / "model-response.json").write_text(json.dumps(transport._scrub({"result": result,
            "usage": usage, "model_actual": actual, "model_evidence": evidence}), ensure_ascii=False, indent=2), encoding="utf-8")
        if actual and actual != request.model and not actual.startswith(request.model + "-"):
            raise ProjectModelError("model_mismatch", "服务返回的型号与请求不一致")
        _validate_schema(result, schema)
        if hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest() != skill_sha:
            raise ValueError("硬件 Skill 在规划期间被修改，请重新生成")
        receipt.update(summary=result["summary"], uncovered=result["uncovered"], step_purposes=result["steps"])
        if result["uncovered"]:
            receipt["status"] = "needs_protocol"
            receipt["plan"] = None
        else:
            actions = {item.id: item for item in request.allowed_actions}
            steps = [{"id": f"step-{index+1:02d}", "function_id": actions[item["action_id"]].function_id,
                      "action_id": item["action_id"], "assertions": [a.model_dump() for a in actions[item["action_id"]].assertions]}
                     for index, item in enumerate(result["steps"])]
            validated = HardwareRunRequest(device=request.device, requirements=request.requirements,
                allowed_actions=request.allowed_actions, plan={"steps": steps}, max_duration_s=request.max_duration_s)
            receipt.update(status="ready", plan=validated.plan.model_dump(),
                binding_sha256=_binding(validated.device, validated.requirements, validated.allowed_actions, validated.plan))
    except Exception as error:
        receipt.update(status="failed", error={"code": getattr(error, "code", "planning_failed"),
            "message": "模型规划未完成；请检查模型配置和协议合同，未执行设备动作。"})
        raise
    finally:
        receipt["duration_s"] = round(time.monotonic() - started, 3)
        receipt = _redact(receipt, secrets)
        (directory / "planning.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def create_hardware_planner_router(settings: Settings) -> APIRouter:
    router = safe_hardware_router(prefix="/api/hardware", tags=["hardware"])

    @router.post("/plan")
    def plan(request: HardwarePlanRequest):
        if not _PLAN_LOCK.acquire(blocking=False):
            raise HTTPException(429, "一个硬件计划正在生成，请等待完成")
        try:
            return generate_hardware_plan(settings, request)
        except ProjectModelError as error:
            raise HTTPException(502, {"code": error.code, "message": str(error)}) from None
        except (OSError, ValueError):
            raise HTTPException(422, "无法生成计划：检查模型配置、设备协议和完整的功能断言；设备尚未执行。") from None
        finally:
            _PLAN_LOCK.release()

    return router
