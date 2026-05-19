"""Architect-reviewer gate — validates fixes and issues verdict."""

from __future__ import annotations

from typing import Any

import httpx

from cca_audit.config import Config


REVIEW_PROMPT = """You are an architect-reviewer performing the final gate review on code changes.

Review the following diff and fix plan. Assess:
1. Completeness — Were all P1/P2 findings actually fixed?
2. Quality — Do the fixes follow project conventions?
3. Correctness — Do the fixes resolve issues without regressions?
4. Security — Did fixes introduce new vulnerabilities?

## Fix Plan
{fixes_content}

## Diff
```
{diff_content}
```

Issue your verdict as one of:
- **APPROVED** — All P1/P2 resolved, no new issues
- **REVISE** — Some fixes are incomplete or incorrect (list specific issues)
- **BLOCKED** — Requires human decision (describe what's needed)

Format your response as:
## Verdict: [APPROVED|REVISE|BLOCKED]

## Assessment
| Area | Status |
|------|--------|
| Completeness | PASS/FAIL |
| Quality | PASS/FAIL |
| Correctness | PASS/FAIL |
| Security | PASS/FAIL |

## Issues (if REVISE)
[List specific issues with file:line and fix instructions]

## Blocker (if BLOCKED)
[Describe what human input is needed]
"""


async def run_review(
    client: httpx.AsyncClient,
    config: Config,
    fixes_content: str,
    diff_content: str,
) -> dict[str, Any]:
    prompt = REVIEW_PROMPT.format(
        fixes_content=fixes_content[:4000],
        diff_content=diff_content[:8000],
    )

    try:
        response = await client.post(
            f"{config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "HTTP-Referer": "https://github.com/GiulioDER/cca-audit",
                "X-Title": "CCA-Audit",
            },
            json={
                "model": config.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]

        verdict = "REVISE"
        if "APPROVED" in content[:200]:
            verdict = "APPROVED"
        elif "BLOCKED" in content[:200]:
            verdict = "BLOCKED"

        return {"verdict": verdict, "content": content}
    except Exception as e:
        return {"verdict": "ERROR", "content": f"Review failed: {e}"}
