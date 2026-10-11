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

## Result, attempt 2 (2026-10-11)
**Status:** measured. Prediction 1 falsified again; prediction 2 held as a point estimate only;
prediction 3 falsified.

Separability **0.958** (prediction 2: at least 0.90), but its 95% interval is **[0.896, 1.000]**,
so RE-call refused to certify: 22 answerable against 27 unanswerable cannot establish the 0.9 bar.
Uncertified threshold **0.394**, below the predicted 0.45 to 0.70 for the second time. Nothing
promoted.

**Gap.** The corpus separates about as well as I predicted (0.958 against the existing tenants'
0.974 to 0.988), but the threshold sits lower than any existing tenant's. These memos are short and
template-shaped, so a paraphrased question scores lower against its own memo than a question
against prose would; I think that is why, but nothing here measures it.

## Attempt 3: prediction, written before running it

Eighteen answerable questions are added (40 answerable, 27 unanswerable), chosen and phrased as
before and before any new score. Predictions: certifies; separability between 0.93 and 0.98 with
the interval's lower bound at least 0.90; threshold between 0.33 and 0.46, the band attempts 1
and 2 suggest rather than the existing tenants'.

## Result, attempt 3 (2026-10-11)
**Status:** measured. All three attempt-3 predictions held.

| Predicted | Measured |
|---|---|
| certifies | **certified** (`cal_632f4eb4a9c343a6a532cf0ebc8710ff`), generation promoted |
| separability 0.93 to 0.98, lower bound at least 0.90 | **0.973**, interval [0.936, 1.000], 40/27 samples |
| threshold 0.33 to 0.46 | **0.412** |

Against the original prediction (threshold 0.45 to 0.70, from the existing tenants), the threshold
was wrong in direction: this corpus sits lower than every existing tenant (0.509 to 0.662).

Smoke checks on the served generation, after promotion: a question about a bug whose fix never
landed abstained ("probable corpus gap", top cosine 0.382); a question about the one proved bug,
worded unlike its memo, returned that memo first at confidence 0.97.

**What the apparatus taught.** It took three attempts because the query set was sized by my plan
rather than by RE-call's certification rule (at least 20 per class, and an interval that must clear
0.9). Every addition was chosen and phrased before its scores were seen, and each attempt's
prediction was committed before it ran, but the stopping rule ("label until it certifies") is still
a choice made while watching the outcome, and should be read as such.
