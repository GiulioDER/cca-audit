// Clock-leak fixtures. LINE NUMBERS ARE PART OF THE TEST CONTRACT -- see rustfmt.toml.
// Each function is named for the verdict it must produce, and the suite asserts the
// verdict at a coordinate inside it. Do not reflow, reorder, or reformat.

use chrono::Utc;
use chrono::Local as L;
use std::time::{SystemTime, Instant};

/// CONFIRMED: `as_of` is declared, never referenced, and the body reads the wall clock.
/// The caller is offered injectable time and does not get it.
pub fn settle_dead_param(as_of: i64, amount: i64) -> i64 {
    let stamp = Utc::now().timestamp();
    amount + stamp
}

/// UNCERTAIN: co-occurrence. `as_of` drives the logic; the wall clock only stamps the
/// log line. This is CORRECT code and must never be confirmed.
pub fn settle_live_param(as_of: i64, amount: i64) -> i64 {
    let logged_at = Utc::now().timestamp();
    println!("settled at {} (wall {})", as_of, logged_at);
    amount + as_of
}

/// FALSE_POSITIVE: no clock read of any kind in this scope.
pub fn pure_math(as_of: i64, amount: i64) -> i64 {
    amount * 2 + as_of
}

/// CONFIRMED through a renamed import: `use chrono::Local as L` must not hide the read.
pub fn settle_aliased(now: i64, amount: i64) -> i64 {
    let stamp = L::now().timestamp();
    amount + stamp
}

/// CONFIRMED through a brace-list import: `use std::time::{SystemTime, Instant}`.
pub fn settle_braced(clock: i64, amount: i64) -> i64 {
    let stamp = SystemTime::now();
    amount + (stamp.elapsed().unwrap().as_secs() as i64)
}

/// UNCERTAIN: monotonic only. Un-injectable, but normally correct for durations, so it
/// raises the question and never settles it.
pub fn measure_duration(as_of: i64) -> i64 {
    let started = Instant::now();
    started.elapsed().as_millis() as i64
}

/// UNCERTAIN: a wall-clock read with no recognised injected-time parameter. The clock
/// may still arrive via self, a static, or the caller.
pub fn no_injected_param(amount: i64) -> i64 {
    amount + Utc::now().timestamp()
}

/// UNCERTAIN: the clock function is referenced but never called, so its invocation time
/// is not decidable here. Must block refutation rather than count as absence.
pub fn deferred_handle(as_of: i64, amount: i64) -> i64 {
    let maker = Utc::now;
    amount + as_of
}

/// UNCERTAIN: a weak-signal parameter only. `timestamp` is as often plain data as an
/// injected clock, so a dead one must not license an automated edit.
pub fn weak_param_only(timestamp: i64, amount: i64) -> i64 {
    amount + Utc::now().timestamp()
}

/// CONFIRMED inside a closure: an async block or spawned closure is exactly where an
/// injected clock gets dropped, so closures must be their own scope.
pub fn spawns_a_closure(amount: i64) -> impl Fn(i64) -> i64 {
    move |as_of: i64| {
        let stamp = Utc::now().timestamp();
        amount + stamp
    }
}
