"""Configuration loading from YAML file and environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    api_key: str = ""
    model: str = "anthropic/claude-sonnet-4-20250514"
    base_url: str = "https://openrouter.ai/api/v1"
    max_tokens: int = 8192
    temperature: float = 0.0
    auditors: list[str] = field(
        default_factory=lambda: ["code", "bug", "security", "perf", "doc", "env", "dep"]
    )
    output_dir: str = ".claude/audits"
    output_format: str = "markdown"
    max_revise_iterations: int = 3
    project_context: str = ""

    @classmethod
    def load(cls, config_path: Path | None = None) -> Config:
        data: dict[str, Any] = {}

        search_paths = [
            Path("cca-audit.yaml"),
            Path("cca-audit.yml"),
            Path(".cca-audit.yaml"),
        ]
        if config_path:
            search_paths.insert(0, config_path)

        for p in search_paths:
            if p.exists():
                with open(p) as f:
                    data = yaml.safe_load(f) or {}
                break

        return cls(
            api_key=os.environ.get("OPENROUTER_API_KEY", data.get("api_key", "")),
            model=os.environ.get("CCA_MODEL", data.get("model", cls.model)),
            base_url=data.get("base_url", cls.base_url),
            max_tokens=int(data.get("max_tokens", cls.max_tokens)),
            temperature=float(data.get("temperature", cls.temperature)),
            auditors=data.get("auditors", cls.auditors),
            output_dir=data.get("output_dir", cls.output_dir),
            output_format=data.get("output_format", cls.output_format),
            max_revise_iterations=int(data.get("max_revise_iterations", cls.max_revise_iterations)),
            project_context=data.get("project_context", cls.project_context),
        )
