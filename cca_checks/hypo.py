"""The determinism contract for generated property files.

Isolated from `properties.py` so that module stays importable without the
optional `hypothesis` dependency. Importing this module when hypothesis is
absent raises ModuleNotFoundError, which `property_check` maps to UNCERTAIN.

Determinism lives here rather than in the generated test: an audit that reports
a different falsifying input on each run is not an artifact, and that guarantee
must not depend on the auditor remembering to write a setting.
"""

from hypothesis import settings

from .properties import MAX_EXAMPLES

cca_settings = settings(
    derandomize=True,
    max_examples=MAX_EXAMPLES,
    deadline=None,  # audit targets may be slow; a deadline would add flaky failures
)
