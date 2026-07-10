from pathlib import Path

import pytest

RULES = Path(__file__).resolve().parent.parent / "cca_checks" / "rules"
CLASSES = ["sql", "command", "code_exec", "path"]


def read(name):
    return (RULES / name).read_text(encoding="utf-8")


def test_rule_files_exist():
    assert (RULES / "python_sinks.yaml").is_file()
    assert (RULES / "python_taint.yaml").is_file()


@pytest.mark.parametrize("cls", CLASSES)
def test_every_class_has_both_tiers(cls):
    sinks = read("python_sinks.yaml")
    assert f"id: sink-strict-{cls}" in sinks
    assert f"id: sink-loose-{cls}" in sinks


@pytest.mark.parametrize("cls", CLASSES)
def test_every_class_has_a_taint_rule(cls):
    assert f"id: taint-{cls}" in read("python_taint.yaml")


def test_sinks_file_declares_no_taint_rules():
    # The sink catalog answers "does a sink occur here", nothing more. A taint rule
    # here would silently turn a presence check into a dataflow claim.
    assert "mode: taint" not in read("python_sinks.yaml")
