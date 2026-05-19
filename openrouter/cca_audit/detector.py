"""Auto-detect project language, framework, test runner, and linter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectInfo:
    languages: list[str] = field(default_factory=list)
    test_cmd: str = ""
    lint_cmd: str = ""
    frameworks: list[str] = field(default_factory=list)

    @property
    def languages_str(self) -> str:
        return ", ".join(self.languages) if self.languages else "Unknown"


EXTENSION_MAP: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
}


def detect_project(root: Path, changed_files: list[str]) -> ProjectInfo:
    info = ProjectInfo()

    seen_langs: set[str] = set()
    for f in changed_files:
        ext = Path(f).suffix
        lang = EXTENSION_MAP.get(ext)
        if lang and lang not in seen_langs:
            seen_langs.add(lang)
            info.languages.append(lang)

    info.test_cmd = _detect_test_cmd(root, info.languages)
    info.lint_cmd = _detect_lint_cmd(root, info.languages)
    info.frameworks = _detect_frameworks(root)

    return info


def _detect_test_cmd(root: Path, langs: list[str]) -> str:
    if "Python" in langs:
        if (root / "pytest.ini").exists() or (root / "conftest.py").exists():
            return "pytest"
        if (root / "pyproject.toml").exists():
            return "pytest"
    if "TypeScript" in langs or "JavaScript" in langs:
        if (root / "package.json").exists():
            return "npm test"
    if "Go" in langs:
        return "go test ./..."
    if "Rust" in langs:
        return "cargo test"
    if "Java" in langs:
        if (root / "pom.xml").exists():
            return "mvn test"
        if (root / "build.gradle").exists():
            return "gradle test"
    if "Ruby" in langs:
        return "bundle exec rspec"
    return ""


def _detect_lint_cmd(root: Path, langs: list[str]) -> str:
    if "Python" in langs:
        if (root / "ruff.toml").exists():
            return "ruff check"
        toml = root / "pyproject.toml"
        if toml.exists() and "[tool.ruff]" in toml.read_text(errors="ignore"):
            return "ruff check"
        return "ruff check"
    if "TypeScript" in langs or "JavaScript" in langs:
        if (root / ".eslintrc.json").exists() or (root / ".eslintrc.js").exists():
            return "eslint ."
        if (root / "biome.json").exists():
            return "biome check ."
    if "Go" in langs:
        return "golangci-lint run"
    if "Rust" in langs:
        return "cargo clippy"
    if "Ruby" in langs:
        return "rubocop"
    return ""


def _detect_frameworks(root: Path) -> list[str]:
    frameworks: list[str] = []
    if (root / "package.json").exists():
        try:
            content = (root / "package.json").read_text(errors="ignore")
            if '"next"' in content:
                frameworks.append("Next.js")
            if '"react"' in content:
                frameworks.append("React")
            if '"express"' in content:
                frameworks.append("Express")
            if '"fastify"' in content:
                frameworks.append("Fastify")
        except OSError:
            pass
    if (root / "manage.py").exists():
        frameworks.append("Django")
    if (root / "Gemfile").exists():
        try:
            content = (root / "Gemfile").read_text(errors="ignore")
            if "rails" in content:
                frameworks.append("Rails")
        except OSError:
            pass
    return frameworks
