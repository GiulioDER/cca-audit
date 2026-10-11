# Pre-registration: does the `cca` RE-call corpus certify?

**Date:** 2026-10-11   **Status:** predicted, not yet measured

## The question

Calibrating the first generation of the `cca` tenant (the 643 bug memos written by
`python -m cca_checks bugs backfill --write`) on its labelled query set: does RE-call certify a
calibration, and with what separability and threshold?

## What I predict

1. **Certifies:** `recall calibration calibrate --publish` succeeds on the first attempt.
2. **Separability at least 0.90.** The three existing tenants measured 0.974 to 0.988 on
   2026-08-25. I expect lower here: these memos are short and alike (same template, same
   vocabulary of fixes), and 643 near-neighbours make answerable and unanswerable scores closer.
3. **Threshold between 0.45 and 0.70**, the band the existing tenants span (0.509 to 0.662).

## What would falsify this

* Calibration refused, or not certified.
* Separability below 0.90.
* A threshold outside 0.45 to 0.70.

## How it will be measured

Generation `gen_1a49a31223c14256bc98fabd8457bd4e` (643 sources, 1,221 chunks, embedder
`voyage-context:voyage-context-4`), query set of 18 answerable and 27 unanswerable questions,
resolved against that generation with no broken label and no unanswerable term found in any memo.
Answerable questions paraphrase a memo's defect and never copy its wording. Metrics by the names
RE-call reports them: certification verdict, separability, threshold.

## What I already know

* Existing tenants (2026-08-25): `memory` 0.509 / 0.974, `re-call-code-gen` 0.662 / 0.988,
  `re-call-docs` 0.637 / 0.976 (threshold / separability).
* This query set is smaller than theirs (45 against 78, 48, 80), so a single mis-ranked query
  moves the figure more.

## Confounds I can name now

* I wrote both the memos' selection and the questions; questions I can phrase easily may be the
  ones the embedder separates easily.
* The unanswerable half is checked only by term absence, not by retrieval, so a question the
  corpus answers with different words would still count as unanswerable and pull the figure down.

## Result, attempt 1 (2026-10-11)
**Status:** measured. Prediction 1 falsified, for a reason the prediction did not consider.

RE-call refused to certify before any separability was computed: "too few samples
(answerable=18; need >= 20 of each)". The uncertified threshold it reported was **0.428**, below
the predicted band, but with 18 answerable points a q05/q95 boundary is not identifiable, so that
number is not evidence about the corpus. Separability: not reported. Nothing was promoted; the
generation stays built and unserved.

**Gap.** The query set's size came from the plan ("at least 15 answerable"), which I wrote
without reading RE-call's certification minimum of 20 per class. The apparatus caught it; the
prediction was about the corpus and never got tested.

## Attempt 2: prediction, written before running it

Four answerable questions are added (22 answerable, 27 unanswerable), chosen from memos in all
four repositories the same way as the first eighteen, before seeing any new score. Predictions 1
to 3 above stand unchanged for this attempt: certifies; separability at least 0.90; threshold
between 0.45 and 0.70.
