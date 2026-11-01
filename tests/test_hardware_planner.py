from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from testfix.config import Settings
from testfix.hardware import HardwareRunRequest
from testfix.hardware_planner import HardwarePlanRequest, generate_hardware_plan, planning_provenance
from testfix.project_models import ProjectModelError


def fixture_request():
    return {"device": {"kind": "http", "base_url": "http://192.0.2.5:8080",
                       "headers": {"Authorization": "local-device-token-not-for-model"}},
        "requirements": [{"id": "led", "description": "读取指示灯状态", "expected": "led=true"}],
        "allowed_actions": [{"kind": "http", "id": "read-led", "function_id": "led",
            "description": "读取指示灯状态并核对状态字段", "method": "GET", "path": "/led/status",
            "assertions": [{"kind": "status_code", "expected": 200},
                           {"kind": "json_equals", "path": ["led"], "expected": True}]}]}


class ModelTransport:
    def __init__(self, result=None, actual=None):
        self.result = result or {"summary": "读取并验证指示灯", "steps": [{"action_id": "read-led", "purpose": "核对声明的状态字段"}], "uncovered": []}
        self.actual = actual
        self.public = None

    def _scrub(self, value):
        return value

    def _invoke_cli(self, prompt, public, schema, timeout):
        self.public = json.loads(public)
        return deepcopy(self.result), {}, self.actual, "requested_cli_flag_only", {"input_tokens": 10, "output_tokens": 10}


def test_model_only_orders_actions_host_preserves_full_response_contract(tmp_path):
    request = HardwarePlanRequest.model_validate(fixture_request())
    transport = ModelTransport()
    settings = Settings(root=tmp_path)
    result = generate_hardware_plan(settings, request, transport=transport)
    assert result["status"] == "ready" and result["model_used"] is True
    assert result["model_actual"] is None
    step = result["plan"]["steps"][0]
    assert step["function_id"] == "led"
    assert step["assertions"] == [item.model_dump() for item in request.allowed_actions[0].assertions]
    wire = json.dumps(transport.public)
    assert "192.0.2.5" not in wire and "local-device-token" not in wire
    assert "/led/status" not in wire
    run = HardwareRunRequest(**fixture_request(), plan=result["plan"], planning_id=result["planning_id"])
    assert planning_provenance(settings, run)["model_used"] is True
    assert (settings.artifacts / "hardware-plans" / result["planning_id"] / "planning.json").is_file()


@pytest.mark.parametrize("mutation", ["new_action", "weaken_expected"])
def test_model_cannot_invent_actions_or_edit_expected_values(tmp_path, mutation):
    transport = ModelTransport()
    if mutation == "new_action":
        transport.result["steps"][0]["action_id"] = "invented-reset-device"
    else:
        transport.result["steps"][0]["assertions"] = []
    with pytest.raises(ProjectModelError):
        generate_hardware_plan(Settings(root=tmp_path), HardwarePlanRequest(**fixture_request()), transport=transport)
    receipt = json.loads(next((tmp_path / "artifacts/hardware-plans").glob("*/planning.json")).read_text())
    assert receipt["status"] == "failed" and receipt["model_used"] is True
    assert "plan" not in receipt


def test_omitted_requirement_prevents_executable_plan(tmp_path):
    data = fixture_request()
    data["requirements"].append({"id": "temperature", "description": "读取温度", "expected": "25"})
    data["allowed_actions"].append({"kind": "http", "id": "read-temperature", "function_id": "temperature",
        "description": "核对温度", "path": "/temperature", "assertions": [{"kind": "json_equals", "expected": 25}]})
    with pytest.raises(ValidationError, match="Uncovered requirement"):
        generate_hardware_plan(Settings(root=tmp_path), HardwarePlanRequest(**data), transport=ModelTransport())


def test_connectivity_only_contract_is_insufficient_before_model_call(tmp_path):
    data = fixture_request()
    data["allowed_actions"][0]["assertions"] = [{"kind": "status_code", "expected": 200}]
    with pytest.raises(ValidationError, match="缺少设备协议"):
        HardwarePlanRequest(**data)
    assert not (tmp_path / "artifacts").exists()


def test_raw_serial_echo_cannot_be_planned_as_a_firmware_feature():
    data = {"device": {"kind": "serial", "port": "COM77"},
        "requirements": [{"id": "led", "description": "验证LED亮起", "expected": "物理状态已反馈"}],
        "allowed_actions": [{"kind": "serial", "id": "echo", "function_id": "led", "description": "回显",
            "write_text": "PING\n", "read_until": "\n", "assertions": [{"kind": "text_equals", "expected": "PING\n"}]}]}
    with pytest.raises(ValidationError, match="缺少设备协议"):
        HardwarePlanRequest(**data)


def test_device_credential_accidentally_copied_into_description_is_not_sent(tmp_path):
    data = fixture_request()
    data["requirements"][0]["description"] += " local-device-token-not-for-model"
    transport = ModelTransport()
    with pytest.raises(ValueError, match="凭据"):
        generate_hardware_plan(Settings(root=tmp_path), HardwarePlanRequest(**data), transport=transport)
    assert transport.public is None
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("value", ['quoted-"token"-value', r'C:\private\device.key'])
def test_json_escaped_credential_values_are_checked_before_serialization(tmp_path, value):
    data = fixture_request()
    data["device"]["headers"]["Authorization"] = value
    data["requirements"][0]["description"] += value
    transport = ModelTransport()
    with pytest.raises(ValueError, match="凭据"):
        generate_hardware_plan(Settings(root=tmp_path), HardwarePlanRequest(**data), transport=transport)
    assert transport.public is None


def test_model_service_credential_in_description_is_not_logged_or_transmitted(tmp_path):
    secret = 'model-credential-"with-quote"'
    data = fixture_request()
    data["requirements"][0]["description"] += secret
    transport = ModelTransport()
    with pytest.raises(ValueError, match="凭据"):
        generate_hardware_plan(Settings(root=tmp_path, api_key=secret), HardwarePlanRequest(**data), transport=transport)
    assert transport.public is None
    assert not (tmp_path / "artifacts").exists()


def test_model_can_report_missing_protocol_without_claiming_execution(tmp_path):
    transport = ModelTransport({"summary": "协议缺少状态语义", "steps": [],
        "uncovered": [{"requirement_id": "led", "reason": "需明确该字段是物理反馈还是命令回显"}]})
    result = generate_hardware_plan(Settings(root=tmp_path), HardwarePlanRequest(**fixture_request()), transport=transport)
    assert result["status"] == "needs_protocol" and result["plan"] is None
    assert result["uncovered"][0]["requirement_id"] == "led"
    assert "success" not in result and "functional_verified" not in result


@pytest.mark.parametrize("change", ["endpoint", "expectation", "description"])
def test_edited_device_or_contract_is_not_labelled_as_original_model_plan(tmp_path, change):
    settings = Settings(root=tmp_path)
    result = generate_hardware_plan(settings, HardwarePlanRequest(**fixture_request()), transport=ModelTransport())
    data = fixture_request()
    plan = deepcopy(result["plan"])
    if change == "endpoint":
        data["device"]["base_url"] = "http://192.0.2.6:8080"
    elif change == "expectation":
        data["allowed_actions"][0]["assertions"][1]["expected"] = False
        plan["steps"][0]["assertions"][1]["expected"] = False
    else:
        data["requirements"][0]["description"] = "另一个不同需求"
    run = HardwareRunRequest(**data, plan=plan, planning_id=result["planning_id"])
    proof = planning_provenance(settings, run)
    assert proof["model_used"] is False and proof["planning_source"] == "manual_or_edited"


def test_ssh_address_command_and_key_paths_do_not_enter_model_snapshot(tmp_path):
    data = fixture_request()
    data["device"] = {"kind": "ssh", "host": "192.0.2.5", "username": "tester",
        "private_key_path": "C:/private/device.key", "known_hosts_path": "C:/private/known_hosts"}
    data["allowed_actions"][0] = {"kind": "ssh", "id": "read-led", "function_id": "led",
        "description": "读取指示灯状态", "command": "device-private-status-command --format=json",
        "assertions": [{"kind": "json_equals", "expected": True, "path": ["led"]}]}
    transport = ModelTransport()
    generate_hardware_plan(Settings(root=tmp_path), HardwarePlanRequest(**data), transport=transport)
    public = json.dumps(transport.public)
    for forbidden in ("192.0.2.5", "device.key", "known_hosts", "device-private-status-command"):
        assert forbidden not in public


def test_serving_model_mismatch_is_retained_as_failure(tmp_path):
    with pytest.raises(ProjectModelError, match="model_mismatch"):
        generate_hardware_plan(Settings(root=tmp_path), HardwarePlanRequest(**fixture_request()),
            transport=ModelTransport(actual="another-model"))
    receipt = json.loads(next((tmp_path / "artifacts/hardware-plans").glob("*/planning.json")).read_text())
    assert receipt["status"] == "failed" and receipt["model_actual"] == "another-model"
