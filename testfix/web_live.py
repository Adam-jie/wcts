"""Read-only live evidence for two fixed, separately deployed web experiments."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import httpx
from fastapi import APIRouter, HTTPException, Query


PROJECTS = {
    "uptime-kuma": {"title": "Uptime Kuma", "source_url": "https://github.com/louislam/uptime-kuma", "ports": (8770, 8771), "marker": "uptime kuma"},
    "paperless-ngx": {"title": "Paperless-ngx", "source_url": "https://github.com/paperless-ngx/paperless-ngx", "ports": (8772, 8773), "marker": "paperless"},
}
LOGS = {"events": "events.jsonl", "install": "install.log", "build": "build.log", "tests": "tests.log",
        "baseline": "baseline-stdout.log", "candidate": "candidate-stdout.log"}
STATES = {"pending", "running", "passed", "failed", "blocked", "completed", "skipped"}


def _redact(value: str) -> str:
    # Consume whole quoted values (including spaces/escaped quotes), or the
    # remaining unquoted line. A JSON log is text here, never executable data.
    value = re.sub(r'''(?is)((?:["']?(?:authorization|api[_-]?key|access[_-]?token|password|secret)["']?\s*[:=]\s*))(?:"(?:\\.|[^"\\])*(?:"|\\?\Z)|'(?:\\.|[^'\\])*(?:'|\\?\Z)|[^\r\n]*)''',
                   r'\1[redacted]', value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[redacted]", value)
    value = re.sub(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]+=*", "[redacted]", value)
    value = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", value)
    return value


def _text(value, limit=1200):
    return _redact(value[:limit]) if isinstance(value, str) else ""


def _safe_file(root: Path, project: str, name: str) -> Path | None:
    path = root / "experiments/web-live" / project / name
    cursor = path
    try:
        while cursor != root:
            if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
                return None
            cursor = cursor.parent
        if not path.is_file() or path.stat().st_nlink > 1:
            return None
    except OSError:
        return None
    return path


def _state(root: Path, project: str) -> dict:
    path = _safe_file(root, project, "status.json")
    if path is None:
        return {}
    try:
        with path.open("rb") as stream:
            raw = stream.read(262145)
        if len(raw) > 262144:
            raise ValueError("oversize")
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {"status": "blocked", "notes": ["状态文件暂不可读；没有据此推断测试通过。"]}


def _items(value):
    result = []
    if isinstance(value, list):
        for item in value[:200]:
            if isinstance(item, dict):
                result.append({"id": _text(item.get("id"), 80), "label": _text(item.get("label"), 160),
                               "status": _status(item.get("status")),
                               "detail": _text(item.get("detail"))})
    return result


def _status(value):
    return value if isinstance(value, str) and value in STATES else "pending"


def _completed_calls(root: Path, project: str) -> int:
    """Count completed local call receipts, independently of reported model ID."""
    completed = 0
    aliases = (("tester-receipt.json",), ("repair-receipt.json", "developer-receipt.json"),
               ("review-receipt.json", "reviewer-receipt.json"), ("ui-tester-receipt.json",),
               ("ui-repair-receipt.json",), ("ui-review-receipt.json",))
    for names in aliases:
        for name in names:
            path = _safe_file(root, project, name)
            if path is None:
                continue
            try:
                with path.open("rb") as stream:
                    raw = stream.read(32769)
                if len(raw) > 32768:
                    continue
                value = json.loads(raw)
                if isinstance(value, dict) and value.get("status") == "completed":
                    completed += 1
                    break
            except (ValueError, OSError):
                continue
    return completed


async def _probe(client: httpx.AsyncClient, base: str, marker: str, frame_policy: dict | None = None) -> bool:
    url = base + "/"
    try:
        for _ in range(3):
            async with client.stream("GET", url, headers={"Accept": "text/html"}) as response:
                if response.is_redirect:
                    target = response.headers.get("location", "")
                    joined = httpx.URL(url).join(target)
                    if joined.scheme != "http" or joined.host != "127.0.0.1" or joined.port != httpx.URL(base).port:
                        return False
                    url = str(joined)
                    continue
                if response.status_code != 200:
                    return False
                if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/html':
                    return False
                if frame_policy is not None:
                    # Respect the tested application's embedding protection.
                    # Conservatively use a standalone tab for any explicit
                    # frame policy rather than loosening its security headers.
                    guarded = bool(response.headers.get('x-frame-options')) or 'frame-ancestors' in response.headers.get('content-security-policy', '').lower()
                    frame_policy[base] = not guarded
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk[:65536 - len(body)])
                    if len(body) >= 65536:
                        break
                return marker in body.decode("utf-8", errors="replace").lower()
    except (httpx.HTTPError, OSError, ValueError):
        return False
    return False


async def _bounded_probe(client: httpx.AsyncClient, base: str, marker: str, frame_policy: dict | None = None) -> bool:
    try:
        return await asyncio.wait_for(_probe(client, base, marker, frame_policy), timeout=2)
    except TimeoutError:
        return False


def create_web_live_router(root: Path) -> APIRouter:
    root = root.resolve()
    router = APIRouter()

    @router.get("/api/web-live")
    async def bootstrap():
        projects = []
        frame_policy = {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(.9), trust_env=False, follow_redirects=False) as client:
            probes = await asyncio.gather(*[_bounded_probe(client, f"http://127.0.0.1:{port}", spec["marker"], frame_policy)
                                          for spec in PROJECTS.values() for port in spec["ports"]])
        for index, (identifier, spec) in enumerate(PROJECTS.items()):
            state = _state(root, identifier)
            model = state.get("model") if isinstance(state.get("model"), dict) else {}
            notes = state.get("notes") if isinstance(state.get("notes"), list) else []
            projects.append({"id": identifier, "title": spec["title"], "source_url": spec["source_url"],
                "source_commit": _text(state.get("source_commit"), 64), "feature": _text(state.get("feature"), 1200),
                "phase": _text(state.get("phase"), 100) or "pending", "status": _status(state.get("status")),
                "updated_at": _text(state.get("updated_at"), 64), "stages": _items(state.get("stages")), "tests": _items(state.get("tests")),
                "notes": [_text(note) for note in notes[:20] if isinstance(note, str)],
                "reference_url": f"http://127.0.0.1:{spec['ports'][0]}", "candidate_url": f"http://127.0.0.1:{spec['ports'][1]}",
                "model": {"requested_model": _text(model.get("requested_model"), 100), "actual_model": _text(model.get("actual_model"), 100) or None,
                          "completed_calls": _completed_calls(root, identifier)},
                "readiness": {"reference": probes[index * 2], "candidate": probes[index * 2 + 1]},
                "embedding": {"reference": probes[index * 2] and frame_policy.get(f"http://127.0.0.1:{spec['ports'][0]}", False),
                              "candidate": probes[index * 2 + 1] and frame_policy.get(f"http://127.0.0.1:{spec['ports'][1]}", False)},
                "readiness_scope": "200 HTML with project marker; business acceptance is recorded separately"})
        return {"projects": projects, "updated_at": datetime.now(timezone.utc).isoformat()}

    @router.get("/api/web-live/{project}/logs")
    def logs(project: str, name: str = Query(default="events")):
        if project not in PROJECTS or name not in LOGS:
            raise HTTPException(404, "Unknown fixed project/log")
        path = _safe_file(root, project, LOGS[name])
        if path is None:
            return {"name": name, "text": "尚无执行日志", "truncated": False}
        try:
            with path.open("rb") as stream:
                size = path.stat().st_size
                # Redact a complete bounded snapshot before tailing: otherwise
                # a cut can discard a multiline secret's field/quote prefix.
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return {"name": name, "text": "日志超过安全读取上限；请查看本机原始文件或任务事件。", "truncated": True}
            text = _redact(raw.decode("utf-8", errors="replace"))
            return {"name": name, "text": text[-16384:], "truncated": size > 16384 or len(text) > 16384}
        except OSError:
            raise HTTPException(409, "Log is temporarily unavailable") from None

    return router
