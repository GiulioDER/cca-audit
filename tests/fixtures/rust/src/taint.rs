// Taint fixtures. LINE NUMBERS ARE PART OF THE TEST CONTRACT -- see rustfmt.toml.
//
// This file is parsed by SEMGREP, not compiled, so it deliberately does not depend on
// any crate. The sink spellings are what the catalog matches on.

use std::process::Command;
use std::fs;

/// UNCERTAIN: a vetted command sink is present in this scope. Semgrep cannot tell a
/// real injection from a safe call, so a hit is evidence, never proof.
pub fn run_report(name: String) -> bool {
    let out = Command::new("sh").arg("-c").arg(name).status();
    out.is_ok()
}

/// FALSE_POSITIVE: no sink of ANY class in this scope, so the finding's premise does
/// not hold. This is the path the parse control protects.
pub fn add_fees(amount: u64, fee: u64) -> u64 {
    amount.saturating_add(fee)
}

/// UNCERTAIN: a vetted path sink.
pub fn load_config(name: String) -> String {
    fs::read_to_string(name).unwrap_or_default()
}

/// UNCERTAIN: an unvetted name that the LOOSE tier catches, so the premise cannot be
/// refuted. `exec_command` is in no strict list and must not read as "no sink".
pub fn dispatch(name: String) -> bool {
    exec_command(name)
}

fn exec_command(_name: String) -> bool {
    true
}
