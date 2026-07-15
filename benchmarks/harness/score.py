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
"""
import os, json, sys

BASE = os.environ.get("CCA_BENCH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOL = 3
# args: [results_json] [bugs_index_json]  (default = BugsInPy run)
RESULTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "results", "wf_output.json")
BUGS = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "harness", "bugs_index.json")


def hit(line, windows):
    try:
        ln = int(line)
    except (TypeError, ValueError):
        return False
    return any(w[0] - TOL <= ln <= w[1] + TOL for w in windows)


def any_hit(findings, windows):
    return any(hit(f.get("line"), windows) for f in (findings or []))


def main():
    bugs_index = json.load(open(BUGS, encoding="utf-8"))
    rows = json.load(open(RESULTS, encoding="utf-8"))
    rows = [r for r in rows if r]

    per_bug = {}
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
            "raw_n": 0, "conf_n": 0, "dropped_n": 0, "catch_lines": []})
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

    bugs = sorted(per_bug.items())
    n = len(bugs)

    def frac(pred):
        return sum(1 for _, p in bugs if pred(p))

    recall_raw = frac(lambda p: p["caught_raw"])
    recall_conf = frac(lambda p: p["caught_conf"])
    spec_conf = frac(lambda p: not p["alarm_conf"])
    rec_yes = [p for _, p in bugs if p["recognized"]]
    rec_no = [p for _, p in bugs if not p["recognized"]]

    print(f"\n{'bug':<16}{'recog':<7}{'caught_raw':<11}{'caught_conf':<12}"
          f"{'false_alarm_conf':<17}{'raw>conf(drop)':<15}")
    print("-" * 78)
    for bug, p in bugs:
        print(f"{bug:<16}{('YES' if p['recognized'] else 'no'):<7}"
              f"{('HIT' if p['caught_raw'] else '.'):<11}"
              f"{('HIT@'+str(sorted(set(p['catch_lines']))) if p['caught_conf'] else '.'):<12}"
              f"{('ALARM' if p['alarm_conf'] else 'quiet'):<17}"
              f"{str(p['raw_n'])+'>'+str(p['conf_n'])+' ('+str(p['dropped_n'])+')':<15}")

    def rate(lst, key):
        return f"{sum(1 for p in lst if p[key])}/{len(lst)}" if lst else "0/0"

    summary = {
        "n_bugs": n, "tolerance_lines": TOL,
        "recall_raw": f"{recall_raw}/{n}", "recall_confirmed": f"{recall_conf}/{n}",
        "specificity_confirmed": f"{spec_conf}/{n}",
        "recall_confirmed_recognized": rate(rec_yes, "caught_conf"),
        "recall_confirmed_clean": rate(rec_no, "caught_conf"),
        "n_recognized": len(rec_yes), "n_clean": len(rec_no),
        "total_raw_findings_buggy": sum(p["raw_n"] for _, p in bugs),
        "total_confirmed_buggy": sum(p["conf_n"] for _, p in bugs),
        "total_dropped_by_fpcheck": sum(p["dropped_n"] for _, p in bugs),
    }
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\nReading: recall_confirmed_clean is the headline honest number "
          "(catch-rate on bugs the model did NOT recognize).")

    outname = "summary_fresh.json" if "fresh" in os.path.basename(RESULTS) else "summary.json"
    json.dump({"summary": summary, "per_bug": {b: p for b, p in bugs}},
              open(os.path.join(BASE, "results", outname), "w"), indent=2)


if __name__ == "__main__":
    main()
