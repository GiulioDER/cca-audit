# Sign trap — settling a numeric finding by execution

A sign error is the bug class that survives review. The expression parses, the variable names are
right, and only the meaning is inverted — so a careful second reading tends to approve it.

`growth.py` computes expected log growth as `(mu + 0.5*vol**2) * t`. The variance term should
reduce growth, not raise it.

## The finding

The numeric-auditor reports it with the property that would expose it:

```yaml
properties:
  - helper: assert_monotonic_in
    target: expected_log_growth
    args: [mu, vol, t]
    index: 1
    direction: decreasing
    delta: 0.1
    domains:
      mu: [-0.5, 0.5]
      vol: [0.01, 1.0]
      t: [0.01, 5.0]
    rationale: variance drag must not raise expected log growth
```

Note what the property states: the *intended* relation, derived from what the function is supposed
to mean. It is not readable off the implementation — which is what stops it being a tautology.

## Settling it

```bash
pip install -e ".[numeric]"
python -m cca_checks numeric --finding-id NUM-001 --test examples/sign-trap/t_NUM-001_props.py
```

```json
{
  "finding_id": "NUM-001",
  "verdict": "CONFIRMED",
  "evidence": "property violated:\nFalsifying example: test_growth_decreases_with_volatility(\nE               # The test always failed when commented parts were varied together.\nE               mu=0.0,  # or any other generated value\nE               vol=1.0,  # or any other generated value\nE               t=1.0,  # or any other generated value\nE           )\nE           cca_checks.properties.PropertyViolation: PROPERTY monotonic violated | inputs=(0.0, 1.0, 1.0) | observed=(0.5, 0.6050000000000001) | required=result non-increasing in arg 1",
  "source": "hypothesis"
}
```

That is an artifact, not an opinion. It reproduces: `derandomize=True` means the same audit
returns the same falsifying input every run.

## What a clean run does NOT mean

If your property holds, the verdict is `UNCERTAIN` — never `FALSE_POSITIVE`. Properties holding
across a bounded search is the absence of a counterexample, not proof of correctness. Pick
`assert_limit` at `vol=0` for this same function and it passes, because the flipped term vanishes
there. The defect is still real; that property just cannot see it.

## After the fix

Keep the property file. Move it into the target's test suite — the property that caught the bug is
the regression test proving the fix satisfies it.
