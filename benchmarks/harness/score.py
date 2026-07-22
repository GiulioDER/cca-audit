#!/usr/bin/env python3
"""Score the CCA x BugsInPy detection run against ground truth.

Metrics (pre-registered):
  - CATCH: an auditor finding localizes within TOL lines of a ground-truth fix
    hunk in the BUGGY file. Reported both RAW (pre-gate) and CONFIRMED (post fp-check).
  - FALSE ALARM: a finding lands on the fix location in the FIXED file (should be
    quiet there). Reported RAW and CONFIRMED.
  - Recall = caught bugs / 12.  Specificity = bugs with no confirmed false alarm / 12.
  - Stratified by recognition (contamination): recognized vs not.
A bug spanning multiple files counts as CAUGHT if any of its files is caught.

DROP ADJUDICATION
-----------------
A bare "fp-check drop rate" is uninterpretable: it sums drops that were the gate
working as designed (killing a hallucination) and drops that cost a real bug, and
it reads as a defect either way. Because this benchmark HAS ground truth, every
drop can be adjudicated deterministically instead of by hand:

  - a drop on the BUGGY file inside a ground-truth window is a WRONG drop -- the
    auditor found the real bug and the gate killed it. If nothing else caught that
    bug, the drop is FATAL and the gate, not the auditor, is why recall missed it.
  - a drop on the FIXED file inside the same window is a CORRECT drop -- the gate
    suppressed a false alarm that would have cost specificity.

So the gate is scored in both directions. Reporting only the drop rate charges the
gate for its successes at exactly the same rate as for its failures.

Drops also carry a structured `drop_reason` (see audit_workflow.js). It separates
REFUTED ("proven not a defect") from INCONCLUSIVE ("could not be proven from this
file"), which are different signals wearing the same number: refutations are the
gate earning its keep, inconclusives are a coverage limit. Results produced before
the field existed score as `unlabeled` rather than being silently counted as
refutations.
"""
import os, json, sys

BASE = os.environ.get("CCA_BENCH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOL = 3
# args: [results_json] [bugs_index_json]  (default = BugsInPy run)
RESULTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "results", "wf_output.json")
BUGS = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "harness", "bugs_index.json")

# Keep in sync with the DROP_REASON enum in harness/audit_workflow.js.
REFUTED_REASONS = ("refuted_guarded_elsewhere", "refuted_misread_flow", "refuted_not_a_defect")
INCONCLUSIVE_REASONS = ("inconclusive_unprovable_from_file",)
OTHER_REASONS = ("duplicate",)
DROP_REASONS = REFUTED_REASONS + INCONCLUSIVE_REASONS + OTHER_REASONS


def hit(line, windows):
    try:
        ln = int(line)
    except (TypeError, ValueError):
        return False
    return any(w[0] - TOL <= ln <= w[1] + TOL for w in windows)


def any_hit(findings, windows):
    return any(hit(f.get("line"), windows) for f in (findings or []))


def count_hits(findings, windows):
    return sum(1 for f in (findings or []) if hit(f.get("line"), windows))


def reason_bucket(finding):
    """Bucket one dropped finding by its `drop_reason`.

    Absent -> `unlabeled` (pre-enum run), unrecognised -> `invalid:<value>`. Neither
    is folded into a valid bucket: a run that cannot say WHY it dropped must not be
    scored as though it had said "refuted".
    """
    raw = finding.get("drop_reason")
    if raw is None or raw == "":
        return "unlabeled"
    if raw in DROP_REASONS:
        return raw
    return f"invalid:{raw}"


def tally_reasons(findings, into):
    for f in findings or []:
        b = reason_bucket(f)
        into[b] = into.get(b, 0) + 1
    return into


def main():
    bugs_index = json.load(open(BUGS, encoding="utf-8"))
    rows = json.load(open(RESULTS, encoding="utf-8"))
    rows = [r for r in rows if r]

    per_bug = {}
    reasons = {}
    for r in rows:
        bug, fpath = r["bug"], r["file"]
        gt = bugs_index.get(bug, {}).get("files", {}).get(fpath)
        if gt is None:
            continue
        bw, fw = gt["buggy_windows"], gt["fixed_windows"]
        pb = per_bug.setdefault(bug, {
            "project": bug.split("/")[0], "recognized": False,
            "caught_raw": False, "caught_conf": False,
            "alarm_raw": False, "alarm_conf": False,
            "raw_n": 0, "conf_n": 0, "dropped_n": 0, "catch_lines": [],
            "wrong_drops": 0, "correct_drops": 0, "drop_verdict": "-"})
        if r.get("recognized") is True:
            pb["recognized"] = True
        if any_hit(r.get("buggy_raw"), bw):
            pb["caught_raw"] = True
        if any_hit(r.get("buggy_confirmed"), bw):
            pb["caught_conf"] = True
            pb["catch_lines"] += [f.get("line") for f in r["buggy_confirmed"] if hit(f.get("line"), bw)]
        if any_hit(r.get("fixed_raw"), fw):
            pb["alarm_raw"] = True
        if any_hit(r.get("fixed_confirmed"), fw):
            pb["alarm_conf"] = True
        pb["raw_n"] += len(r.get("buggy_raw") or [])
        pb["conf_n"] += len(r.get("buggy_confirmed") or [])
        pb["dropped_n"] += len(r.get("buggy_dropped") or [])
        # Adjudicate the gate in both directions against the same ground truth.
        pb["wrong_drops"] += count_hits(r.get("buggy_dropped"), bw)
        pb["correct_drops"] += count_hits(r.get("fixed_dropped"), fw)
        tally_reasons(r.get("buggy_dropped"), reasons)
        tally_reasons(r.get("fixed_dropped"), reasons)

    # A wrong drop is FATAL when no surviving finding caught the bug: the gate, not
    # the auditor, is the reason recall missed it. If something else still caught it,
    # the drop was redundant -- worth seeing, but it cost no recall.
    for _, p in per_bug.items():
        if p["wrong_drops"]:
            p["drop_verdict"] = "redundant" if p["caught_conf"] else "FATAL"

    bugs = sorted(per_bug.items())
    n = len(bugs)

    def frac(pred):
        return sum(1 for _, p in bugs if pred(p))

    recall_raw = frac(lambda p: p["caught_raw"])
    recall_conf = frac(lambda p: p["caught_conf"])
    spec_conf = frac(lambda p: not p["alarm_conf"])
    bugs_lost_to_gate = frac(lambda p: p["drop_verdict"] == "FATAL")
    rec_yes = [p for _, p in bugs if p["recognized"]]
    rec_no = [p for _, p in bugs if not p["recognized"]]

    print(f"\n{'bug':<16}{'recog':<7}{'caught_raw':<11}{'caught_conf':<12}"
          f"{'false_alarm_conf':<17}{'raw>conf(drop)':<15}{'gate':<10}")
    print("-" * 88)
    for bug, p in bugs:
        print(f"{bug:<16}{('YES' if p['recognized'] else 'no'):<7}"
              f"{('HIT' if p['caught_raw'] else '.'):<11}"
              f"{('HIT@'+str(sorted(set(p['catch_lines']))) if p['caught_conf'] else '.'):<12}"
              f"{('ALARM' if p['alarm_conf'] else 'quiet'):<17}"
              f"{str(p['raw_n'])+'>'+str(p['conf_n'])+' ('+str(p['dropped_n'])+')':<15}"
              f"{p['drop_verdict']:<10}")

    def rate(lst, key):
        return f"{sum(1 for p in lst if p[key])}/{len(lst)}" if lst else "0/0"

    total_dropped = sum(p["dropped_n"] for _, p in bugs)
    wrong_drops = sum(p["wrong_drops"] for _, p in bugs)
    correct_drops = sum(p["correct_drops"] for _, p in bugs)
    labeled = sum(v for k, v in reasons.items() if k in DROP_REASONS)
    refuted = sum(reasons.get(k, 0) for k in REFUTED_REASONS)
    inconclusive = sum(reasons.get(k, 0) for k in INCONCLUSIVE_REASONS)

    def over_labeled(x):
        return f"{x}/{labeled}" if labeled else "0/0 (no drop_reason in this run)"

    summary = {
        "n_bugs": n, "tolerance_lines": TOL,
        "recall_raw": f"{recall_raw}/{n}", "recall_confirmed": f"{recall_conf}/{n}",
        "specificity_confirmed": f"{spec_conf}/{n}",
        "recall_confirmed_recognized": rate(rec_yes, "caught_conf"),
        "recall_confirmed_clean": rate(rec_no, "caught_conf"),
        "recall_raw_clean": rate(rec_no, "caught_raw"),
        "n_recognized": len(rec_yes), "n_clean": len(rec_no),
        "total_raw_findings_buggy": sum(p["raw_n"] for _, p in bugs),
        "total_confirmed_buggy": sum(p["conf_n"] for _, p in bugs),
        "total_dropped_by_fpcheck": total_dropped,
        # --- gate adjudication (both directions) ---
        "wrong_drops_on_ground_truth": wrong_drops,
        "bugs_lost_to_gate": bugs_lost_to_gate,
        "false_alarms_prevented_by_gate": correct_drops,
        "drop_reasons": dict(sorted(reasons.items())),
        "refuted": over_labeled(refuted),
        "inconclusive": over_labeled(inconclusive),
    }
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\nReading: recall_confirmed_clean is the headline honest number "
          "(catch-rate on bugs the model did NOT recognize).")
    print("The drop rate alone is not a defect rate. bugs_lost_to_gate is the cost of "
          "the gate; false_alarms_prevented_by_gate is what it bought.")
    if reasons.get("unlabeled"):
        print(f"NOTE: {reasons['unlabeled']} drop(s) carry no drop_reason (pre-enum run) -- "
              "refuted/inconclusive are not separable for those.")

    outname = "summary_fresh.json" if "fresh" in os.path.basename(RESULTS) else "summary.json"
    json.dump({"summary": summary, "per_bug": {b: p for b, p in bugs}},
              open(os.path.join(BASE, "results", outname), "w"), indent=2)


if __name__ == "__main__":
    main()
