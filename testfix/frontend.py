"""Serve the built local workbench with a per-response stylesheet nonce."""
from __future__ import annotations

import base64
from pathlib import Path
import secrets

from fastapi import Request
from fastapi.responses import HTMLResponse

MARKER = "__TESTFIX_STYLE_NONCE__"


def hardware_document(root: Path, request: Request) -> HTMLResponse:
    path = root / "web/hardware-app/index.html"
    if not path.is_file():
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>前端需要构建</title>'
            '<h1>硬件工作台尚未构建</h1><p>在 frontend 目录执行 pnpm install 与 pnpm build 后刷新。</p></html>', status_code=503)
    source = path.read_text(encoding="utf-8")
    if source.count(MARKER) != 1:
        return HTMLResponse("前端产物版本不匹配，请重新构建工作台。", status_code=503)
    nonce = base64.b64encode(secrets.token_bytes(18)).decode("ascii")
    request.state.style_nonce = nonce
    return HTMLResponse(source.replace(MARKER, nonce))


def web_testing_document(root: Path, request: Request) -> HTMLResponse:
    path = root / "web/hardware-app/web-testing.html"
    if not path.is_file():
        return HTMLResponse("网页系统工作台尚未构建，请在 frontend 执行 pnpm build。", status_code=503)
    source = path.read_text(encoding="utf-8")
    if source.count(MARKER) != 1:
        return HTMLResponse("工作台产物版本不匹配，请重新构建。", status_code=503)
    nonce = base64.b64encode(secrets.token_bytes(18)).decode("ascii")
    request.state.style_nonce = nonce
    return HTMLResponse(source.replace(MARKER, nonce))


def engineering_document(root: Path, request: Request) -> HTMLResponse:
    path = root / 'web/hardware-app/engineering.html'
    if not path.is_file():
        return HTMLResponse('工程协作工作台尚未构建，请在 frontend 执行 pnpm build。', status_code=503)
    source = path.read_text(encoding='utf-8')
    if source.count(MARKER) != 1:
        return HTMLResponse('工作台产物版本不匹配，请重新构建。', status_code=503)
    nonce = base64.b64encode(secrets.token_bytes(18)).decode('ascii')
    request.state.style_nonce = nonce
    return HTMLResponse(source.replace(MARKER, nonce))
