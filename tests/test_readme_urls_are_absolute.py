"""README.md is the PyPI long description -- every reference in it must be absolute.

GitHub resolves a relative path against the repository. PyPI does not: it
renders the same markdown standalone, so `docs/banner.svg` resolves against
pypi.org and 404s. The failure is invisible in every place an author looks --
the file renders perfectly on GitHub, `twine check` passes (it validates that
the markup *parses*, not that the targets exist), and the wheel installs fine.

It is also unrepairable in place. **PyPI freezes a version's description at
upload**, so a broken link on the project page can only be fixed by shipping a
new version number. That is what 0.7.1 was: 0.7.0 shipped with a broken banner
image and 15 relative links resolving to `https://pypi.org/project/cca-audit/...`
(verified 404). The same defect class cost `recall-rag` a release too.

So this is a test rather than a review note. Anchors (`#section`) are exempt --
they resolve within the rendered page on both hosts, which is exactly what they
are for.
"""

import pathlib
import re

_README = pathlib.Path(__file__).resolve().parent.parent / "README.md"

# `#anchor` works on both hosts; mailto: has no host to resolve against.
_ABSOLUTE_PREFIXES = ("http://", "https://", "#", "mailto:")

_HTML_IMG_SRC = re.compile(r"<img[^>]*\ssrc=\"([^\"]+)\"", re.IGNORECASE)
_HTML_A_HREF = re.compile(r"<a[^>]*\shref=\"([^\"]+)\"", re.IGNORECASE)
# Markdown inline links and images share this shape; the leading `!` for an
# image does not change how the target resolves, so both are collected here.
_MARKDOWN_TARGET = re.compile(r"\]\(([^)\s]+)")


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _relative_references() -> list[tuple[int, str]]:
    text = _README.read_text(encoding="utf-8")
    found: list[tuple[int, str]] = []
    for pattern in (_HTML_IMG_SRC, _HTML_A_HREF, _MARKDOWN_TARGET):
        for match in pattern.finditer(text):
            target = match.group(1).strip()
            if not target.startswith(_ABSOLUTE_PREFIXES):
                found.append((_line_of(text, match.start()), target))
    return sorted(set(found))


def test_readme_has_no_relative_references():
    """Every link and image in README.md must be absolute, or it 404s on PyPI.

    Asserts the invariant -- "nothing relative" -- rather than listing today's
    known-bad paths, so a link added next month is covered without anyone
    remembering this file exists.
    """
    relative = _relative_references()

    assert not relative, (
        "README.md is published verbatim as the PyPI long description, where "
        "relative paths resolve against pypi.org and 404. Make these absolute "
        "(https://github.com/GiulioDER/cca-audit/blob/master/<path>, or "
        "raw.githubusercontent.com for images). PyPI freezes a version's "
        "description at upload, so shipping this requires burning a version "
        "number to fix:\n"
        + "\n".join(f"  - README.md:{line}  {target}" for line, target in relative)
    )


def test_the_guard_itself_detects_a_relative_reference():
    """The check must be able to fail.

    A regex that silently stops matching would leave `test_readme_has_no_
    relative_references` passing forever on a broken README -- a green check
    that verifies nothing, which is the failure mode this whole project exists
    to prevent. Exercise the detection path, not just the green one.
    """
    sample = '<img src="docs/banner.svg"/>\nSee [the design](docs/v3-design.md) and [PyPI](https://pypi.org).\n'
    hits = []
    for pattern in (_HTML_IMG_SRC, _HTML_A_HREF, _MARKDOWN_TARGET):
        for match in pattern.finditer(sample):
            target = match.group(1).strip()
            if not target.startswith(_ABSOLUTE_PREFIXES):
                hits.append(target)

    assert sorted(set(hits)) == ["docs/banner.svg", "docs/v3-design.md"]
