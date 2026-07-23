// A repro that ACTUALLY PANICS. LINE NUMBERS are not contract here -- what matters is
// the panic message, which is what `--expect-error` matches against.
//
// This is the confirmation path for claim types clippy may only refute: a lint sees
// `.unwrap()` or `qty * price`, not whether it is reachable with a value the caller
// controls. Executing it is what settles that.
//
// Debug builds panic on integer overflow; release builds wrap silently. This test can
// only ever demonstrate the first, which is why the wrapping half is disclosed as an
// honest limit rather than claimed.
use cca_clippy_fixture::notional;

#[test]
fn notional_overflows_on_caller_controlled_input() {
    let _ = notional(u64::MAX, 2);
}
