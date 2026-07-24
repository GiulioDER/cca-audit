// Refutation blockers: files where NO clock read is visible, but the file could have
// hidden one. Every function here must escalate rather than refute -- a file must not
// earn a FALSE_POSITIVE carrying an authoritative `source` by being hard to read.
//
// LINE NUMBERS ARE PART OF THE TEST CONTRACT -- see rustfmt.toml. Do not reformat.

use chrono::prelude::*;

/// UNCERTAIN, not FALSE_POSITIVE. There is no clock read in this scope, but the glob
/// `use` above means an unqualified `now()` could resolve through it. This is the
/// Rust analogue of Python's `from x import *`, which clock_check already treats as
/// unrefutable.
pub fn looks_clean_under_a_glob(as_of: i64, amount: i64) -> i64 {
    amount * 2 + as_of
}

/// UNCERTAIN, not FALSE_POSITIVE. tree-sitter does not expand macros, so a clock read
/// inside `log_event!` is invisible to it. Python has no equivalent of this blocker.
pub fn looks_clean_beside_a_macro(as_of: i64, amount: i64) -> i64 {
    log_event!("settling {}", amount);
    amount * 2 + as_of
}
