// Clippy-backend fixtures. LINE NUMBERS ARE PART OF THE TEST CONTRACT -- see
// rustfmt.toml. Do not reflow, reorder, or reformat.
//
// UNLIKE tests/fixtures/rust, THIS CRATE MUST COMPILE. clippy only lints code that
// type-checks, so a fixture with a syntax or type error produces no diagnostics at
// all -- which reads exactly like a clean crate and would turn every assertion here
// green for the wrong reason.
//
// The crate-level `allow` below is deliberate and load-bearing: it is the audited
// repo suppressing the evidence against itself. `--force-warn` must beat it, and
// `test_force_warn_beats_the_crates_own_allow` is what proves it does.

#![allow(clippy::unwrap_used)]
#![allow(clippy::arithmetic_side_effects)]

/// `panic_path`: `.unwrap()` on a caller-controlled slice. Clippy reports the
/// construct; whether it is REACHABLE is not decidable from a lint, so this may
/// only ever be UNCERTAIN -- never CONFIRMED.
pub fn first_fill(fills: &[u64]) -> u64 {
    let head = fills.first().unwrap();
    *head
}

/// `panic_path` refutation: no panicking construct anywhere in this scope.
pub fn first_fill_checked(fills: &[u64]) -> u64 {
    match fills.first() {
        Some(head) => *head,
        None => 0,
    }
}

/// `overflow`: raw multiplication on a money path. In debug this panics; in release
/// it wraps silently, which is the nastier half and is NOT provable by a debug test.
pub fn notional(qty: u64, price: u64) -> u64 {
    qty * price
}

/// `overflow` refutation: saturating arithmetic, which cannot overflow.
///
/// Spelled with `saturating_mul` rather than `checked_mul` plus a fallback, because
/// clippy has a lint for every spelling of the fallback: `.unwrap_or(u64::MAX)` draws
/// `manual_saturating_arithmetic` and a `match` draws `manual_unwrap_or`. Both are
/// real diagnostics at the cited line and neither is in OVERFLOW_LINTS, so the
/// checker escalates on them -- correctly, since an unrecognised lint must never read
/// as "no bug". A REFUTE fixture therefore has to be clean of EVERY lint, not merely
/// of the overflow ones.
pub fn notional_saturating(qty: u64, price: u64) -> u64 {
    qty.saturating_mul(price)
}

/// `error_swallow`: the ledger write's Result is discarded. The lint fires on the
/// defect ITSELF, not on a possibility, so this one may CONFIRM.
pub fn settle(amount: u64) -> u64 {
    let _ = write_ledger(amount);
    amount
}

/// `error_swallow` refutation: the Result is propagated.
pub fn settle_propagating(amount: u64) -> Result<u64, String> {
    write_ledger(amount)?;
    Ok(amount)
}

fn write_ledger(amount: u64) -> Result<u64, String> {
    if amount == 0 {
        return Err("zero".to_string());
    }
    Ok(amount)
}
