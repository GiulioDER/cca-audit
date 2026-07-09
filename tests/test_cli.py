import json
import cca_checks.__main__ as cli

def test_definedness_cli(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_pyright", lambda path: [])  # pyright silent = defined
    rc = cli.main(["definedness", "--finding-id", "ENV-1", "--file", "sizer.py", "--line", "12"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"] == "FALSE_POSITIVE" and out["finding_id"] == "ENV-1"

def test_repro_cli(capsys):
    rc = cli.main(["repro", "--finding-id", "BUG-1",
                   "--test", "tests/fixtures/raises_fixture.py", "--expect-error", "ZeroDivisionError"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"] == "CONFIRMED"
