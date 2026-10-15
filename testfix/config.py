"""Configuration with optional legacy .env mapping; never persist secret values."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    provider: str = "replay"
    runner: str = "process"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    llm_timeout: float = 120.0
    max_tokens: int = 4096
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    agentarts_url: str = ""
    agentarts_key: str = ""
    docker_image: str = "python:3.13-slim"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def dataset(self) -> Path:
        return self.root / "data" / "humaneval"

    def providers(self) -> list[dict]:
        return [
            {"id": "replay", "label": "离线回放（仅验证工程流程）", "configured": True},
            {"id": "codex_session", "label": "GPT-5.6 会话结果（由 Codex 生成后导入）", "configured": False},
            {"id": "openai", "label": f"兼容模型 API · {self.model or '未配置'}", "configured": bool(self.base_url and self.model and self.api_key)},
            {"id": "ollama", "label": f"Ollama · {self.ollama_model or '未配置'}", "configured": bool(self.ollama_model)},
            {"id": "agentarts", "label": "华为云 AgentArts Runtime", "configured": bool(self.agentarts_url and self.agentarts_key)},
        ]


def load_settings(root: Path = ROOT) -> Settings:
    load_dotenv(root / ".env", override=False)
    legacy_path = os.getenv("TESTFIX_LEGACY_ENV", "")
    legacy = dotenv_values(legacy_path) if legacy_path else {}

    def mapped(name: str, mapping: str, default_var: str) -> str:
        if os.getenv(name):
            return os.environ[name]
        variable = os.getenv(mapping, default_var)
        return str(os.getenv(variable) or legacy.get(variable) or "")

    return Settings(
        root=root,
        provider=os.getenv("TESTFIX_PROVIDER", "replay"),
        runner=os.getenv("TESTFIX_RUNNER", "process"),
        base_url=mapped("TESTFIX_LLM_BASE_URL", "TESTFIX_LEGACY_BASE_VAR", "OPENAI_BASE_URL"),
        model=mapped("TESTFIX_LLM_MODEL", "TESTFIX_LEGACY_MODEL_VAR", "OPENAI_MODEL"),
        api_key=mapped("TESTFIX_LLM_API_KEY", "TESTFIX_LEGACY_KEY_VAR", "OPENAI_API_KEY"),
        llm_timeout=float(os.getenv("TESTFIX_LLM_TIMEOUT", "120")),
        max_tokens=int(os.getenv("TESTFIX_LLM_MAX_TOKENS", "4096")),
        ollama_url=os.getenv("TESTFIX_OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.getenv("TESTFIX_OLLAMA_MODEL", ""),
        agentarts_url=os.getenv("TESTFIX_AGENTARTS_URL", ""),
        agentarts_key=os.getenv("TESTFIX_AGENTARTS_API_KEY", ""),
        docker_image=os.getenv("TESTFIX_DOCKER_IMAGE", "python:3.13-slim"),
    )

