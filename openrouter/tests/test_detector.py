"""Tests for language/framework auto-detection."""

from pathlib import Path

from cca_audit.detector import detect_project


def test_detect_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    info = detect_project(tmp_path, ["app.py", "utils/helper.py"])
    assert "Python" in info.languages
    assert info.test_cmd == "pytest"
    assert info.lint_cmd == "ruff check"


def test_detect_typescript(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18"}}')
    info = detect_project(tmp_path, ["src/app.tsx", "src/utils.ts"])
    assert "TypeScript" in info.languages
    assert info.test_cmd == "npm test"
    assert "React" in info.frameworks


def test_detect_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    info = detect_project(tmp_path, ["main.go", "handler.go"])
    assert "Go" in info.languages
    assert info.test_cmd == "go test ./..."
    assert info.lint_cmd == "golangci-lint run"


def test_detect_rust(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'app'\n")
    info = detect_project(tmp_path, ["src/main.rs", "src/lib.rs"])
    assert "Rust" in info.languages
    assert info.test_cmd == "cargo test"
    assert info.lint_cmd == "cargo clippy"


def test_detect_multi_language(tmp_path: Path) -> None:
    info = detect_project(tmp_path, ["app.py", "frontend/app.tsx", "scripts/build.go"])
    assert len(info.languages) == 3


def test_no_files(tmp_path: Path) -> None:
    info = detect_project(tmp_path, [])
    assert info.languages == []
    assert info.languages_str == "Unknown"
