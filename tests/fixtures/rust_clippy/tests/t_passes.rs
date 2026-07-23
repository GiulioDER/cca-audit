// The negative control: a repro that does NOT reproduce. Without one, a runner that
// confirmed unconditionally would satisfy every other assertion.
use cca_clippy_fixture::notional;

#[test]
fn notional_is_fine_on_small_inputs() {
    assert_eq!(notional(3, 4), 12);
}
