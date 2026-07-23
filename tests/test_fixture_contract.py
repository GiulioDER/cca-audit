"""The acceptance fixtures' LINE NUMBERS are part of the test contract.

The suite settles claims at specific coordinates in these files, so anything that
shifts a line -- a formatter, an import rewrite, an editor stripping a blank line --
silently changes what is being tested. A `ruff --fix` pass once rewrote
`Optional[Card]` to `Card | None` in `unguarded_optional.py`, dropped the
now-unused `typing` import, moved the defect from line 9 to line 8, and turned a
CONFIRMED acceptance test into an UNCERTAIN. The suite still went green everywhere
else, and the only symptom was one confusing failure.

`tests/fixtures` is excluded from ruff for this reason. These assertions are the
backstop: they pin the coordinate itself, so the next such shift fails loudly and
points straight at the cause instead of looking like a checker regression.
"""

import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _line(name: str, number: int) -> str:
    """1-indexed line `number` of a fixture, stripped."""
    text = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    assert len(text) >= number, f"{name} has {len(text)} lines; expected at least {number}"
    return text[number - 1].strip()


@pytest.mark.parametrize("name,number,expected", [
    # The unguarded deref the nullability acceptance test confirms at line 9.
    ("unguarded_optional.py", 9, "return card.token"),
    # The guarded counterpart the same suite expects to be refuted at line 11.
    ("guarded_optional.py", 11, "return card.token"),
    # --- Rust clock-leak coordinates. Same contract, enforced by rustfmt.toml's
    # `disable_all_formatting` rather than by ruff's extend-exclude.
    ("rust/src/clock.rs", 12, "let stamp = Utc::now().timestamp();"),
    ("rust/src/clock.rs", 26, "amount * 2 + as_of"),
    ("rust/src/clock.rs", 31, "let stamp = L::now().timestamp();"),
    ("rust/src/clock.rs", 71, "let stamp = Utc::now().timestamp();"),
    ("rust/src/clock_blockers.rs", 14, "amount * 2 + as_of"),
    ("rust/src/clock_blockers.rs", 20, 'log_event!("settling {}", amount);'),
    # --- Rust taint coordinates, mirrored as constants in test_rust_taint.py.
    ("rust/src/taint.rs", 12, 'let out = Command::new("sh").arg("-c").arg(name).status();'),
    ("rust/src/taint.rs", 19, "amount.saturating_add(fee)"),
    ("rust/src/taint.rs", 24, "fs::read_to_string(name).unwrap_or_default()"),
    ("rust/src/taint.rs", 30, "exec_command(name)"),
    # --- clippy-backend coordinates, mirrored as constants in test_clippy_check.py.
    ("rust_clippy/src/lib.rs", 20, "let head = fills.first().unwrap();"),
    ("rust_clippy/src/lib.rs", 27, "Some(head) => *head,"),
    ("rust_clippy/src/lib.rs", 35, "qty * price"),
    ("rust_clippy/src/lib.rs", 48, "qty.saturating_mul(price)"),
    ("rust_clippy/src/lib.rs", 54, "let _ = write_ledger(amount);"),
    ("rust_clippy/src/lib.rs", 60, "write_ledger(amount)?;"),
])
def test_fixture_defect_is_on_the_expected_line(name, number, expected):
    assert _line(name, number) == expected, (
        f"{name}:{number} no longer holds {expected!r}. A formatter or import "
        f"rewrite has shifted the fixture; the acceptance tests settle claims at "
        f"these exact coordinates."
    )


@pytest.mark.parametrize("crate", ["rust", "rust_clippy"])
def test_the_rust_fixtures_forbid_formatting(crate):
    """rustfmt is the Rust half of the ruff `extend-exclude` rule in pyproject.toml.

    Without it, a `cargo fmt` over the workspace reflows these files and moves every
    defect off its coordinate -- the same failure a `ruff --fix` pass already caused
    once on the Python side, and just as silent.
    """
    config = (FIXTURES / crate / "rustfmt.toml").read_text(encoding="utf-8")
    assert "disable_all_formatting = true" in config


def test_optional_fixtures_keep_the_typing_import_form():
    """`Optional[...]` here is deliberate, not stale style.

    Rewriting it to `X | None` removes the `typing` import line and shifts every
    line below it -- which is precisely how the coordinates drifted before.
    """
    for name in ("unguarded_optional.py", "guarded_optional.py"):
        src = (FIXTURES / name).read_text(encoding="utf-8")
        assert "from typing import Optional" in src, (
            f"{name} lost its `from typing import Optional` line; every line below "
            f"it has shifted and the acceptance coordinates are now wrong."
        )
