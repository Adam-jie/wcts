"""Lightweight hardware IO child: validated fixed actions only, no web/model stack.

The parent validates transport schemas, immutable contracts and resource locks.
This module deliberately imports optional transports only when executing them.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any

def _response(data: bytes, **extra) -> dict:
    text = data.decode("utf-8", errors="replace")
    try:
        def finite(value):
            number = float(value)
            if not math.isfinite(number): raise ValueError("Non-finite JSON number")
            return number
        def constant(value): raise ValueError("Non-standard JSON constant")
        def unique_pairs(pairs):
            result = dict(pairs)
            if len(result) != len(pairs): raise ValueError("Duplicate JSON object keys")
            return result
        value = json.loads(text, parse_float=finite, parse_constant=constant, object_pairs_hook=unique_pairs)
        json_valid = True
    except ValueError:
        value = None
        json_valid = False
    return {"communication_passed": True, "bytes_read": len(data), "text": text,
            "hex": data.hex(), "json": value, "json_valid": json_valid, **extra}


def _http(device: Any, action: Any) -> dict:
    import httpx
    data = bytearray()
    with httpx.Client(verify=device.verify_tls, trust_env=False, follow_redirects=False,
                      timeout=action.timeout_s) as client:
        with client.stream(action.method, device.base_url + action.path,
                           headers={**device.headers, **action.headers}, json=action.json_body) as response:
            for chunk in response.iter_bytes(chunk_size=1024):
                if len(data) + len(chunk) > action.max_bytes:
                    raise ValueError("HTTP response exceeds its declared byte budget")
                data.extend(chunk)
            return _response(bytes(data), status_code=response.status_code, redirect_followed=False,
                             verify_tls=device.verify_tls)


def _serial(device: Any, action: Any) -> dict:
    import serial
    if device.port == "loop://":
        connection = serial.serial_for_url("loop://", do_not_open=True)
    else:
        connection = serial.Serial(port=None)
    connection.baudrate = device.baudrate
    connection.timeout = min(.1, action.timeout_s)
    connection.write_timeout = min(1, action.timeout_s)
    connection.dtr, connection.rts = device.dtr, device.rts
    if device.port != "loop://":
        connection.port = device.port
    deadline, data = time.monotonic() + action.timeout_s, bytearray()
    outgoing = bytes.fromhex(action.write_hex) if action.write_hex is not None else action.write_text.encode("utf-8")
    until = (bytes.fromhex(action.read_until_hex) if action.read_until_hex is not None
             else action.read_until.encode("utf-8") if action.read_until is not None else None)
    try:
        connection.open()
        written = connection.write(outgoing)
        if written != len(outgoing):
            raise ValueError("Serial write was incomplete")
        while time.monotonic() < deadline and len(data) < action.max_bytes:
            chunk = connection.read(min(256, action.max_bytes - len(data)))
            if chunk:
                data.extend(chunk)
                if until is not None and until in data:
                    data = data[:data.index(until) + len(until)]
                    break
            elif data and until is None:
                break
        if len(data) == action.max_bytes and until is not None and until not in data:
            raise ValueError("Serial response reached its byte budget before a complete frame")
        complete = bool(data) and (until is None or until in data)
        return _response(bytes(data), bytes_written=written, port_opened=True, dtr=device.dtr, rts=device.rts,
                         communication_passed=complete, frame_complete=complete)
    finally:
        connection.close()


def _ssh(device: Any, action: Any) -> dict:
    import paramiko
    key, known = Path(device.private_key_path).expanduser(), Path(device.known_hosts_path).expanduser()
    if not key.is_file() or not known.is_file():
        raise ValueError("Explicit SSH private key or known-hosts file is missing")
    client = paramiko.SSHClient()
    channel, stdout, stderr = None, bytearray(), bytearray()
    try:
        client.load_host_keys(str(known))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(device.host, port=device.port, username=device.username, key_filename=str(key),
                       timeout=action.timeout_s, banner_timeout=action.timeout_s, auth_timeout=action.timeout_s,
                       channel_timeout=action.timeout_s, allow_agent=False, look_for_keys=False)
        channel = client.get_transport().open_session(timeout=action.timeout_s)
        channel.settimeout(action.timeout_s)
        channel.exec_command(action.command)
        deadline = time.monotonic() + action.timeout_s
        while time.monotonic() < deadline:
            if channel.recv_ready(): stdout.extend(channel.recv(1024))
            if channel.recv_stderr_ready(): stderr.extend(channel.recv_stderr(1024))
            if len(stdout) + len(stderr) > action.max_bytes:
                raise ValueError("SSH output exceeds its declared byte budget")
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                return _response(bytes(stdout), stderr=bytes(stderr).decode("utf-8", "replace"),
                                 exit_code=channel.recv_exit_status(), host_key_policy="RejectPolicy")
            time.sleep(.01)
        raise TimeoutError("SSH command deadline exceeded")
    finally:
        if channel is not None: channel.close()
        client.close()


def action_worker() -> None:
    try:
        value = json.loads(sys.stdin.read(150_000))
        device = SimpleNamespace(**value["device"])
        action = SimpleNamespace(**value["action"])
        if device.kind != action.kind or device.kind not in {"http", "serial", "ssh"}:
            raise ValueError("Transport/action mismatch")
        result = {"http": _http, "serial": _serial, "ssh": _ssh}[device.kind](device, action)
    except Exception as error:
        result = {"communication_passed": False, "error": type(error).__name__ + ": " + str(error)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
