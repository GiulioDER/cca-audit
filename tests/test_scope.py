import textwrap

from cca_checks.scope import enclosing_span


def write(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
    return str(p)


def test_enclosing_span_finds_the_function_containing_the_line(tmp_path):
    path = write(tmp_path, "m.py", """
        def a():
            x = 1
            return x

        def b(card):
            return card.token
    """)
    # after dedent().lstrip(): line 5 is `def b`, line 6 is the access
    assert enclosing_span(path, 6) == (5, 6)
    assert enclosing_span(path, 2) == (1, 3)


def test_enclosing_span_picks_the_innermost_function(tmp_path):
    path = write(tmp_path, "m.py", """
        def outer():
            def inner():
                return 1
            return inner
    """)
    assert enclosing_span(path, 3) == (2, 3)


def test_enclosing_span_falls_back_to_the_module(tmp_path):
    path = write(tmp_path, "m.py", """
        X = 1
        Y = 2
    """)
    lo, hi = enclosing_span(path, 1)
    assert lo == 1 and hi >= 2


def test_enclosing_span_handles_async_functions(tmp_path):
    path = write(tmp_path, "m.py", """
        async def fetch(card):
            return card.token
    """)
    assert enclosing_span(path, 2) == (1, 2)


def test_enclosing_span_on_a_line_past_eof_falls_back_to_the_module(tmp_path):
    path = write(tmp_path, "m.py", "X = 1\n")
    lo, hi = enclosing_span(path, 999)
    assert lo == 1 and hi >= 1
