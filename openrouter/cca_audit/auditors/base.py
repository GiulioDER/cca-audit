"""Base auditor abstract class."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader

from cca_audit.config import Config
from cca_audit.detector import ProjectInfo


class BaseAuditor(ABC):
    name: str = ""
    prefix: str = ""
    output_file: str = ""

    def __init__(self, config: Config, project: ProjectInfo):
        self.config = config
        self.project = project
        self._template_env = Environment(
            loader=FileSystemLoader(str(Path(__file__).parent.parent / "prompts")),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    @abstractmethod
    def template_name(self) -> str:
        ...

    def build_prompt(self, files: list[str], diff_cmd: str, diff_content: str) -> str:
        template = self._template_env.get_template(self.template_name())
        return template.render(
            files=files,
            file_count=len(files),
            languages=self.project.languages_str,
            diff_cmd=diff_cmd,
            diff_content=diff_content[:8000],
            project_context=self.config.project_context,
            prefix=self.prefix,
        )

    async def run(
        self, client: httpx.AsyncClient, files: list[str], diff_cmd: str, diff_content: str
    ) -> dict[str, Any]:
        start = time.monotonic()
        prompt = self.build_prompt(files, diff_cmd, diff_content)

        try:
            response = await client.post(
                f"{self.config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "HTTP-Referer": "https://github.com/GiulioDER/cca-audit",
                    "X-Title": "CCA-Audit",
                },
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            duration = time.monotonic() - start

            return {
                "auditor": self.name,
                "status": "COMPLETE",
                "duration": round(duration, 1),
                "content": content,
                "output_file": self.output_file,
            }
        except Exception as e:
            duration = time.monotonic() - start
            return {
                "auditor": self.name,
                "status": "ERROR",
                "duration": round(duration, 1),
                "content": f"# {self.name} Auditor\n\nError: {e}",
                "output_file": self.output_file,
                "error": str(e),
            }
