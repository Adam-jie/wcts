"""User-declared hardware actions, immutable test contracts and bounded adapters.

The model may select and order action IDs. It cannot create endpoints, commands,
serial writes or expected outcomes. No discovery connection, flashing or network
scan is performed. Each action runs in a deadline-owned local child process.
"""
from __future__ import annotations

from collections import defaultdict
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import sys
import threading
import time
from typing import Annotated, Any, Callable, Literal
from urllib.parse import urlsplit
import uuid

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .config import Settings
from .package_tools import _run_bounded
from .reports import now


MAX_BYTES = 65536
_IDS = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_SERIAL = re.compile(r"(?:COM[1-9][0-9]{0,3}|/dev/(?:tty|cu)[A-Za-z0-9_.-]{1,80})\Z", re.I)
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
FUNCTIONAL_KINDS = {"text_contains", "text_equals", "hex_equals", "json_equals"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _identifier(value: str) -> str:
    if not _IDS.fullmatch(value):
        raise ValueError("IDs must be short ASCII letters, digits, dot, underscore or hyphen")
    return value


def _hex(value: str) -> str:
    compact = "".join(value.split()).lower()
    if not compact or len(compact) % 2 or not re.fullmatch(r"[0-9a-f]+", compact):
        raise ValueError("Hex data must contain complete nonempty byte pairs")
    return compact


def _host(value: str) -> str:
    # IP literals prevent unbound DNS resolution/rebinding and still allow LANs.
    try:
        address = ipaddress.ip_address(value)
        if address.is_unspecified or address.is_multicast:
            raise ValueError("A single explicit device address is required")
        return str(address)
    except ValueError:
        raise ValueError("Use the device's explicit IPv4 or IPv6 address") from None


def _headers(value: dict[str, str]) -> dict[str, str]:
    if len(value) > 20 or sum(len(k) + len(v) for k, v in value.items()) > 8192:
        raise ValueError("Headers exceed the fixed size budget")
    for name, content in value.items():
        if (not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}", name)
                or any(c in content for c in "\r\n\x00")
                or name.lower() in {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}):
            raise ValueError("Unsafe HTTP header")
    return value


class HTTPDevice(Strict):
    kind: Literal["http"]
    name: str = Field(default="MPU HTTP", max_length=80)
    base_url: str = Field(max_length=1024)
    headers: dict[str, str] = Field(default_factory=dict)
    verify_tls: bool = True

    @field_validator("base_url")
    @classmethod
    def base_allowed(cls, value):
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or parts.path not in {"", "/"}
                or "\\" in value or any(ord(c) < 33 for c in value)):
            raise ValueError("Base URL must be http(s)://IP[:port] without credentials or a path")
        _host(parts.hostname)
        try:
            port = parts.port
        except ValueError:
            raise ValueError("Invalid device port") from None
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Invalid device port")
        return value.rstrip("/")

    _check_headers = field_validator("headers")(_headers)


class SSHDevice(Strict):
    kind: Literal["ssh"]
    name: str = Field(default="MPU SSH", max_length=80)
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    private_key_path: str = Field(min_length=1, max_length=2048)
    known_hosts_path: str = Field(min_length=1, max_length=2048)

    _check_host = field_validator("host")(_host)


class SerialDevice(Strict):
    kind: Literal["serial"]
    name: str = Field(default="MCU Serial", max_length=80)
    port: str = Field(max_length=128)
    baudrate: int = Field(default=115200, ge=50, le=4_000_000)
    dtr: bool = False
    rts: bool = False

    @field_validator("port")
    @classmethod
    def port_allowed(cls, value):
        if value != "loop://" and not _SERIAL.fullmatch(value):
            raise ValueError("Use an explicit COM/tty/cu device, or exact loop:// for simulation")
        return value.upper() if value.upper().startswith("COM") else value


Device = Annotated[HTTPDevice | SSHDevice | SerialDevice, Field(discriminator="kind")]


class Requirement(Strict):
    id: str
    description: str = Field(min_length=1, max_length=2000)
    expected: str = Field(min_length=1, max_length=2000)
    category: Literal["functional", "communication"] = "functional"

    _check_id = field_validator("id")(_identifier)


class Assertion(Strict):
    kind: Literal["status_code", "exit_code", "text_contains", "text_equals", "hex_equals", "json_equals"]
    expected: JsonValue
    path: list[str | int] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def bounded_contract(self):
        if len(json.dumps(self.expected, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 32768:
            raise ValueError("Expected result exceeds the assertion byte budget")
        if self.kind in {"status_code", "exit_code"} and type(self.expected) is not int:
            raise ValueError("Status/exit expectations must be integers")
        if self.kind == "status_code" and not 100 <= self.expected <= 599:
            raise ValueError("HTTP status must be between 100 and 599")
        if self.kind in {"text_contains", "text_equals", "hex_equals"}:
            if not isinstance(self.expected, str) or not self.expected:
                raise ValueError("Text/hex expectations must be nonempty strings")
        if self.kind == "hex_equals":
            self.expected = _hex(self.expected)
        if self.kind != "json_equals" and self.path:
            raise ValueError("Only JSON assertions accept a key path")
        if any(type(part) not in {int, str} or isinstance(part, str) and len(part) > 256 for part in self.path):
            raise ValueError("JSON key paths must contain bounded strings or integers")
        return self


class ActionBase(Strict):
    id: str
    function_id: str
    description: str = Field(min_length=1, max_length=2000)
    timeout_s: float = Field(default=3, ge=.1, le=10)
    max_bytes: int = Field(default=4096, ge=1, le=MAX_BYTES)
    assertions: list[Assertion] = Field(default_factory=list, max_length=16)

    _check_id = field_validator("id", "function_id")(_identifier)


class HTTPAction(ActionBase):
    kind: Literal["http"]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    path: str = Field(max_length=2048)
    json_body: JsonValue = None
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        parts = urlsplit(value)
        if (not value.startswith("/") or value.startswith("//") or parts.scheme or parts.netloc
                or parts.fragment or "\\" in value or any(ord(c) < 33 for c in value)):
            raise ValueError("HTTP action path must stay relative to the declared device origin")
        return value

    @field_validator("json_body")
    @classmethod
    def bounded_body(cls, value):
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 32768:
            raise ValueError("HTTP body exceeds 32KiB")
        return value

    _check_headers = field_validator("headers")(_headers)


class SSHAction(ActionBase):
    kind: Literal["ssh"]
    command: str = Field(min_length=1, max_length=4096)

    @field_validator("command")
    @classmethod
    def command_bytes(cls, value):
        if "\x00" in value:
            raise ValueError("SSH command contains NUL")
        return value


class SerialAction(ActionBase):
    kind: Literal["serial"]
    write_text: str | None = Field(default=None, max_length=4096)
    write_hex: str | None = Field(default=None, max_length=8192)
    read_until: str | None = Field(default=None, max_length=32)
    read_until_hex: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def serial_bytes(self):
        if (self.write_text is None) == (self.write_hex is None):
            raise ValueError("Declare exactly one serial write_text or write_hex")
        if self.write_hex is not None:
            self.write_hex = _hex(self.write_hex)
        if self.read_until is not None and self.read_until_hex is not None:
            raise ValueError("Declare at most one serial delimiter")
        if self.read_until_hex is not None:
            self.read_until_hex = _hex(self.read_until_hex)
        if self.read_until == "":
            raise ValueError("Serial delimiter cannot be empty")
        return self


Action = Annotated[HTTPAction | SSHAction | SerialAction, Field(discriminator="kind")]


class TestStep(Strict):
    id: str
    function_id: str
    action_id: str
    assertions: list[Assertion] = Field(max_length=16)

    _check_ids = field_validator("id", "function_id", "action_id")(_identifier)


class TestPlan(Strict):
    steps: list[TestStep] = Field(min_length=1, max_length=32)


class HardwareRunRequest(Strict):
    device: Device
    requirements: list[Requirement] = Field(min_length=1, max_length=32)
    allowed_actions: list[Action] = Field(min_length=1, max_length=32)
    plan: TestPlan
    planning_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    max_duration_s: float = Field(default=60, ge=1, le=120)

    @model_validator(mode="after")
    def constrained_plan(self):
        requirements = {item.id: item for item in self.requirements}
        actions = {item.id: item for item in self.allowed_actions}
        if len(requirements) != len(self.requirements) or len(actions) != len(self.allowed_actions):
            raise ValueError("Requirement IDs and action IDs must be unique")
        if len({step.id for step in self.plan.steps}) != len(self.plan.steps):
            raise ValueError("Step IDs must be unique")
        for action in self.allowed_actions:
            if action.kind != self.device.kind or action.function_id not in requirements:
                raise ValueError("Every allowed action must match the declared transport and requirement")
            for assertion in action.assertions:
                if (assertion.kind == "status_code" and action.kind != "http"
                        or assertion.kind == "exit_code" and action.kind != "ssh"):
                    raise ValueError("Assertion does not match the action transport")
        covered = defaultdict(list)
        for step in self.plan.steps:
            action = actions.get(step.action_id)
            if action is None or step.function_id != action.function_id:
                raise ValueError("Plan may only reference declared actions for the same function_id")
            if not _equal_json([item.model_dump() for item in step.assertions],
                               [item.model_dump() for item in action.assertions]):
                raise ValueError("Model steps must preserve the user's complete action assertion contract")
            covered[step.function_id].append(action)
        for identifier, requirement in requirements.items():
            if not covered[identifier]:
                raise ValueError("Uncovered requirement: " + identifier)
            if requirement.category == "functional" and not any(
                    _functional_assertion(action, assertion) for action in covered[identifier] for assertion in action.assertions):
                raise ValueError("Functional requirement lacks a selected user-declared behavior contract: " + identifier)
        return self


def hardware_capabilities() -> dict:
    serial_available = importlib.util.find_spec("serial") is not None
    ports = []
    if serial_available:
        from serial.tools import list_ports
        ports = [{"port": item.device, "description": item.description} for item in list_ports.comports()]
    return {"transports": {"http": {"available": True, "dependency": "httpx"},
                            "serial": {"available": serial_available, "dependency": "pyserial"},
                            "ssh": {"available": importlib.util.find_spec("paramiko") is not None, "dependency": "paramiko"}},
            "serial_ports": ports, "loopback": {"port": "loop://", "is_simulation": True},
            "limits": {"steps": 32, "action_timeout_s": 10, "run_timeout_s": 120, "response_bytes": MAX_BYTES},
            "notice": "只枚举本地串口，不连接设备或扫描网络；回显/HTTP联通不等于固件功能已验证。"}


def _device_key(device: Device) -> str:
    if isinstance(device, HTTPDevice):
        parts = urlsplit(device.base_url)
        return "ip:" + _host(parts.hostname)
    if isinstance(device, SSHDevice):
        return "ip:" + device.host
    return "serial:" + device.port.lower()


def _simulation(device: Device) -> bool:
    if isinstance(device, SerialDevice):
        return device.port == "loop://"
    host = urlsplit(device.base_url).hostname if isinstance(device, HTTPDevice) else device.host
    return ipaddress.ip_address(host).is_loopback


def _public_device(device: Device) -> dict:
    if isinstance(device, HTTPDevice):
        return {"kind": device.kind, "name": device.name, "base_url": device.base_url, "verify_tls": device.verify_tls}
    if isinstance(device, SSHDevice):
        return {"kind": device.kind, "name": device.name, "host": device.host, "port": device.port,
                "username": device.username, "host_key_policy": "RejectPolicy", "key_contents_recorded": False}
    return device.model_dump()


def _equal_json(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_equal_json(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_equal_json(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _functional_assertion(action: Action, assertion: Assertion) -> bool:
    if assertion.kind not in FUNCTIONAL_KINDS:
        return False
    if not isinstance(action, SerialAction):
        return True
    outgoing = (bytes.fromhex(action.write_hex) if action.write_hex is not None
                else action.write_text.encode("utf-8"))
    # A simple echo adapter can satisfy these contracts without implementing
    # firmware behavior. Require a distinguishable response for functional tests.
    if assertion.kind in {"text_contains", "text_equals"}:
        return assertion.expected.encode("utf-8") not in outgoing
    if assertion.kind == "hex_equals":
        return bytes.fromhex(assertion.expected) not in outgoing
    try:
        value = json.loads(outgoing.decode("utf-8"))
        for key in assertion.path:
            value = value[key]
        return not _equal_json(value, assertion.expected)
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        return True


def _assert(assertion: Assertion, actual: dict) -> dict:
    kind, expected = assertion.kind, assertion.expected
    value = actual.get({"status_code": "status_code", "exit_code": "exit_code", "text_contains": "text",
                        "text_equals": "text", "hex_equals": "hex", "json_equals": "json"}[kind])
    error = None
    if kind == "json_equals":
        if actual.get("json_valid") is not True:
            error = "Response is not valid JSON"
        try:
            for key in assertion.path:
                if type(key) is int and not isinstance(value, list) or type(key) is str and not isinstance(value, dict):
                    raise KeyError(key)
                value = value[key]
        except (KeyError, IndexError, TypeError):
            error = "JSON path not found"
    passed = (isinstance(value, str) and expected in value) if kind == "text_contains" else _equal_json(value, expected)
    return {"kind": kind, "scope": "functional" if kind in FUNCTIONAL_KINDS else "communication",
            "path": assertion.path, "expected": expected, "actual": value, "passed": bool(passed and error is None),
            **({"error": error} if error else {})}


_WORKER = "import sys;sys.path.insert(0,sys.argv[1]);from testfix.hardware_io import action_worker;action_worker()"


def _execute(device: Device, action: Action, remaining: float) -> dict:
    timeout = min(action.timeout_s, remaining)
    process = _run_bounded([sys.executable, "-I", "-B", "-X", "utf8", "-c", _WORKER,
                            str(Path(__file__).resolve().parents[1])],
                           cwd=Path(__file__).resolve().parents[1], timeout_s=timeout,
                           stdin=json.dumps({"device": device.model_dump(),
                                             "action": action.model_dump(exclude={"assertions"})}, ensure_ascii=False))
    if process["timeout"] or process["output_limit_exceeded"]:
        return {"communication_passed": False, "error": "Action deadline or output budget exceeded",
                "timeout": process["timeout"], "duration_ms": round(process["duration_s"] * 1000)}
    if process["exit_code"] != 0:
        return {"communication_passed": False, "error": "Hardware adapter process failed", "duration_ms": round(process["duration_s"] * 1000)}
    try:
        value = json.loads(process["stdout"])
    except ValueError:
        return {"communication_passed": False, "error": "Hardware adapter returned invalid evidence"}
    value["duration_ms"] = round(process["duration_s"] * 1000)
    return value


def _redact(value: Any, secrets: list[str], *, data_keys: bool = False) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        json_assertion = value.get("kind") == "json_equals"
        return {(_redact(key, secrets) if data_keys else key):
                _redact(item, secrets, data_keys=data_keys or key == "json"
                        or json_assertion and key in {"expected", "actual"})
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets, data_keys=data_keys) for item in value]
    return value


def _output_dir(settings: Settings, identifier: str) -> Path:
    root = settings.root.resolve()
    path = root / "artifacts/hardware-runs" / identifier
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Hardware report paths may not use links")
        if item == root:
            break
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_hardware(settings: Settings, request: HardwareRunRequest, *, planning_provenance: dict | None = None) -> dict:
    request = HardwareRunRequest.model_validate(request.model_dump())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(_device_key(request.device), threading.Lock())
    if not lock.acquire(blocking=False):
        raise RuntimeError("This device already has an active test; wait for its completion")
    started = time.monotonic()
    identifier = uuid.uuid4().hex
    report = {"schema": "testfix.hardware-run.v1", "run_id": identifier, "created_at": now(),
              "status": "failed", "success": False, "functional_verified": False,
              "device": _public_device(request.device), "is_simulation": _simulation(request.device),
              "model_used": False, "planning_source": "manual_or_edited" if request.planning_id else "manual",
              "steps": [], "coverage": [], "requirements": [item.model_dump() for item in request.requirements],
              "notice": "通讯通过仅表示连接/协议交换；功能结果以用户声明的行为断言为准。模拟结果不验证真实固件。"}
    if planning_provenance:
        for key in ("model_used", "planning_source", "model_requested", "model_actual", "planning_id"):
            if key in planning_provenance:
                report[key] = planning_provenance[key]
        if "source" in planning_provenance:
            report["planning_source"] = planning_provenance["source"]
    secrets = list(request.device.headers.values()) if isinstance(request.device, HTTPDevice) else []
    header_groups = [request.device.headers] if isinstance(request.device, HTTPDevice) else []
    for action in request.allowed_actions:
        if isinstance(action, HTTPAction):
            secrets.extend(action.headers.values())
            header_groups.append(action.headers)
    for headers in header_groups:
        for name, value in headers.items():
            if name.lower() == "authorization" and " " in value:
                secrets.append(value.split(" ", 1)[1])
    if isinstance(request.device, SSHDevice):
        secrets.extend([request.device.private_key_path, request.device.known_hosts_path])
    output = None
    try:
        output = _output_dir(settings, identifier)
        actions = {action.id: action for action in request.allowed_actions}
        deadline = started + request.max_duration_s
        for step in request.plan.steps:
            action = actions[step.action_id]
            if time.monotonic() >= deadline:
                report["steps"].append({"id": step.id, "function_id": step.function_id, "action_id": step.action_id,
                                        "status": "failed", "error": "Run deadline exceeded", "assertions": [], "duration_ms": 0})
                continue
            actual = _execute(request.device, action, deadline - time.monotonic())
            assertions = [_assert(item, actual) for item in action.assertions]
            for assertion, result in zip(action.assertions, assertions):
                result["scope"] = "functional" if _functional_assertion(action, assertion) else "communication"
            passed = actual.get("communication_passed") is True and all(item["passed"] for item in assertions)
            public_action = {"kind": action.kind, "id": action.id, "description": action.description}
            if isinstance(action, HTTPAction):
                public_action.update(method=action.method, path=action.path)
            elif isinstance(action, SSHAction):
                public_action["command"] = action.command
            else:
                public_action.update(write_text=action.write_text, write_hex=action.write_hex,
                                     read_until=action.read_until, read_until_hex=action.read_until_hex)
            report["steps"].append({"id": step.id, "function_id": step.function_id, "action_id": step.action_id,
                                    "status": "passed" if passed else "failed", "expected": [item.model_dump() for item in action.assertions],
                                    "actual": actual, "assertions": assertions, "request": public_action,
                                    "communication_passed": actual.get("communication_passed") is True,
                                    "duration_ms": actual.get("duration_ms", 0)})
        for requirement in request.requirements:
            matches = [step for step in report["steps"] if step["function_id"] == requirement.id]
            passed = bool(matches) and all(step["status"] == "passed" for step in matches)
            report["coverage"].append({"function_id": requirement.id, "covered": bool(matches), "passed": passed,
                                        "category": requirement.category, "steps": [step["id"] for step in matches]})
        report["success"] = all(item["covered"] and item["passed"] for item in report["coverage"])
        report["status"] = "passed" if report["success"] else "failed"
        report["functional_verified"] = (report["success"] and not report["is_simulation"]
                                          and any(item.category == "functional" for item in request.requirements))
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        try:
            report["duration_s"] = round(time.monotonic() - started, 3)
            report["validation"] = {"passed": sum(item["status"] == "passed" for item in report["steps"]),
                                     "total": len(request.plan.steps), "status": report["status"]}
            report = _redact(report, secrets)
            if output:
                report["report_path"] = output.relative_to(settings.root.resolve()).as_posix() + "/report.json"
                (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        finally:
            lock.release()
    return report


def safe_hardware_router(**options):
    """Keep credential-bearing input out of FastAPI's validation responses."""
    from fastapi import APIRouter
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from fastapi.routing import APIRoute

    class SafeHardwareRoute(APIRoute):
        def get_route_handler(self):
            original = super().get_route_handler()
            async def handler(request):
                try:
                    return await original(request)
                except RequestValidationError as error:
                    errors = [{key: item[key] for key in ("loc", "msg", "type") if key in item}
                              for item in error.errors()]
                    return JSONResponse(status_code=422, content={"detail": errors})
            return handler
    return APIRouter(route_class=SafeHardwareRoute, **options)


def create_hardware_router(settings: Settings, planning_resolver: Callable[[HardwareRunRequest], dict] | None = None):
    from fastapi import HTTPException
    router = safe_hardware_router(prefix="/api/hardware", tags=["hardware"])

    @router.get("/capabilities")
    def capabilities():
        return hardware_capabilities()

    @router.post("/validate-plan")
    def validate_plan(request: HardwareRunRequest):
        return {"valid": True, "steps": len(request.plan.steps), "requirements": len(request.requirements),
                "covered_function_ids": sorted({step.function_id for step in request.plan.steps}),
                "is_simulation": _simulation(request.device), "model_used": False}

    @router.post("/run")
    def run(request: HardwareRunRequest):
        try:
            provenance = planning_resolver(request) if planning_resolver else None
            return run_hardware(settings, request, planning_provenance=provenance)
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from None
        except OSError:
            raise HTTPException(503, "Hardware evidence could not be persisted; the device lock was released") from None

    return router


# Public adapter aliases keep tests and callers on the same implementation.
from .hardware_io import _response, _http, _serial, _ssh, action_worker as _action_worker
