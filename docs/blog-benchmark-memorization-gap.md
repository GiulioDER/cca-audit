# Your AI code reviewer's benchmark score is probably inflated — here's how much

*A small, honest benchmark of an AI bug-hunter on memorized vs. novel code — and the 40-point gap between them.*

Every AI code tool ships a number. "Finds N% of bugs." The trouble is that almost every public bug benchmark was fixed in public, years ago, and now sits in the training data of the model doing the finding. So when an AI auditor flags a bug in a famous library, you can't tell whether it *reasoned about the code* or *remembered the patch*.

We built a way to tell those two apart, ran it on our own tool, and the gap was bigger than we expected: **83% on the benchmark the model had memorized, 43% on bugs it hadn't.** Here's the method, the numbers, and the parts that didn't flatter us.

## What we tested

[CCA](https://github.com/GiulioDER/cca-audit) is an open-source multi-agent code auditor for Claude Code. Its one genuinely distinguishing feature isn't the multi-agent fan-out — that's commodity now — it's an **anti-hallucination gate** (`fp-check`) that re-checks every finding against the real code and drops the ones it can't prove, *before* they reach you.

We ran the same pipeline two ways:

1. **BugsInPy** — 12 bugs sampled (seeded, blind) from a well-known 2018–2020 Python bug benchmark (pandas, matplotlib, tornado, …).
2. **Fresh** — 10 real bug-fix PRs merged *after* the model's training cutoff, in a spread of repos from 241-star niche projects to aiohttp and pipx.

## The method (reproducible)

For each bug we take the **fixing commit's own diff** as ground truth — the exact lines a human changed to fix it. Then, on both the **buggy** and the **fixed** version of each file, we run the auditors **blind** (they're never told a bug exists or where). Scoring:

- **Catch** = a finding lands within ±3 lines of the human's fix, on the buggy file.
- **Specificity** = the auditor stays *quiet* at that spot on the fixed file. (A tool that just pattern-matches "this looks risky" flags both; a tool that detects the *actual defect* flags one and shuts up on the other.)

And one extra step that turns out to matter more than anything else:

- **A recognition probe.** Before trusting a single catch, we ask the auditor a separate question: *do you recognize this exact file — could you reproduce its canonical version from memory?* Because if it can, it isn't finding the bug. It's finding the diff.

## Result 1: the classic benchmark is memorized

On BugsInPy, confirmed recall was **10/12 (83%)** with **perfect specificity (12/12)**. A great number — until you read the recognition probe, which came back **`recognized = true` for all 12 files.** In the auditors' own words:

> *"The diff confirms the single injected defect. The rest of the file matches upstream matplotlib exactly."*

> *"I can spot injected/buggy deviations by comparison rather than pure first-principles reasoning."*

That's not bug detection. That's memory recall. **The 83% is a contamination score.**

## Result 2: novel code, and the 40-point gap

On the fresh post-cutoff bugs, the recognition probe dropped to **3/10 recognized** — and on the 7 it genuinely did *not* recognize (including aiohttp and pipx, despite their fame — the probe discriminates at the *code* level, not by star count), it reported honestly:

> *"I do NOT have a line-level memory of this specific implementation … I could not find bugs by diffing against a known-correct copy — only by first-principles reasoning."*

On those 7 clean bugs, confirmed recall was **3/7 (43%)**:

| Repo | The bug it caught | Proof |
|------|-------------------|-------|
| **aio-libs/aiohttp** | HTTP/1.1 request without a Host header not rejected | [PR #12264](https://github.com/aio-libs/aiohttp/pull/12264) |
| **allaboutduncan/clu-comics** | wanted-issue matching not month-agnostic | [PR #389](https://github.com/allaboutduncan/clu-comics/pull/389) |
| **pypa/pipx** | local `--find-links` not resolved | [PR #1860](https://github.com/pypa/pipx/pull/1860) |

**83% memorized → 43% novel.** That 40-point gap is the number nobody reports, because measuring it requires admitting your benchmark is contaminated.

## Result 3: the gate earns its keep exactly where it should

The anti-hallucination gate dropped **17% of raw findings on memorized code, but 42% on novel code.** That's the point: on unfamiliar code the auditors speculate more, and the gate is there to catch them before you are. "Doesn't cry wolf," measured on the corpus where crying wolf is most likely.

## The parts that don't flatter us

If we're going to accuse everyone else of inflated numbers, ours had better be honest:

- **Pilot scale.** Seven clean bugs is small; 3/7 has wide error bars. This is a method and a signal, not a leaderboard.
- **The gate costs catches too.** On one bug (satpy #3367) a raw finding *did* localize to the fix — and `fp-check` **dropped it**. The gate that kills false positives also killed one true one. That trade-off is real and we're not hiding it.
- **One false alarm.** A 318 KB file (clu-comics) produced a confirmed finding on the *fixed* version inside the fix window — possibly a genuine nearby bug, possibly noise.
- **Localization, not fix-correctness.** We measure whether it finds and localizes the bug, not whether it would write the right patch.

## The takeaway

If you're evaluating an AI code tool — yours or someone's you're about to buy — **ask whether the eval measured recognition.** If it didn't, discount the headline number, because some unknown fraction of it is the model remembering a public fix.

And if you're building one: an AI auditor's honest value on *novel* code is real but modest. Pair it with a gate that drops what it can't prove, and report the number from code the model has never seen. That's the only number that transfers to your codebase.

---

*CCA is open-source (MIT): [github.com/GiulioDER/cca-audit](https://github.com/GiulioDER/cca-audit). The full benchmark harness — seeded sampler, `gh`-based materializer, the parallel audit workflow, and the scorer — is reproducible; every catch above links to the real merged PR. Check them.*
