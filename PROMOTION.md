# Promotion Strategy

## Launch Channels

### 1. Hacker News — Show HN

**Title:** Show HN: CCA-Audit -- 6-layer parallel code audit pipeline powered by LLMs

**Post body:**
> We built an open-source audit pipeline that runs 6 specialized LLM auditors in parallel on your codebase. Each auditor has a non-overlapping scope (code quality, bugs, security, performance, docs, config), so you get zero duplicate findings.
>
> The pipeline deduplicates, prioritizes (P1/P2/P3), auto-fixes critical issues, re-verifies with tests+lint, and gates through an architect review.
>
> Three variants: Claude Code (drop-in agents), Codex CLI (shell orchestrator), and a standalone Python CLI via OpenRouter (works with any model).
>
> GitHub: [link]

**Best time to post:** Tuesday-Thursday, 8-10am ET

### 2. Reddit

| Subreddit | Angle |
|-----------|-------|
| r/programming | Technical: "How non-overlapping scopes eliminate duplicate audit findings" |
| r/ChatGPTCoding | Practical: "One command to audit your entire codebase with 6 parallel LLM agents" |
| r/ClaudeAI | Claude-specific: "Drop-in Claude Code agents for automated code auditing" |
| r/MachineLearning | Architecture: "Parallel LLM agents with scope isolation for code analysis" |
| r/devops | CI integration: "Add LLM-powered code auditing to your PR pipeline" |

### 3. Twitter/X Thread

1. "We open-sourced our code audit pipeline. 6 LLM agents run in parallel, each with a non-overlapping scope. Zero duplicate findings. [screenshot of pipeline diagram]"
2. "The key insight: giving each agent exclusive ownership of a domain (security, bugs, perf, etc.) eliminates the #1 problem with LLM code review -- contradictory and duplicate findings."
3. "Three ways to use it: Claude Code (one slash command), Codex CLI (shell script), or any model via OpenRouter (pip install cca-audit)."
4. "Built this for a production system where every commit goes through 6 parallel auditors before deploy. Open-sourced the pipeline. [link]"

### 4. Dev.to / Hashnode Article

**Title:** "How We Built a 6-Layer AI Code Audit Pipeline (And Why Each Auditor Has Its Own Scope)"

**Outline:**
1. The problem: LLM code review gives duplicate/contradictory findings
2. The solution: non-overlapping scope design
3. Pipeline architecture (with Mermaid diagram)
4. The 6 auditors and their boundaries
5. Deduplication algorithm
6. Auto-fix + verification loop
7. Results: how it works in production
8. How to install and use

### 5. GitHub

**Topics:** `code-audit`, `llm-tools`, `claude-code`, `code-review`, `ai-code-review`, `static-analysis`, `openai-codex`, `openrouter`

**Description:** "6-layer parallel code audit pipeline powered by LLMs. 6 specialized auditors with non-overlapping scopes. Claude Code, Codex CLI, and OpenRouter API variants."

### 6. ProductHunt

**Tagline:** "6 AI auditors review your code in parallel -- zero duplicate findings"

**Description:** Focus on the three variants and the "works with any model" angle via OpenRouter.

**Maker comment:** Explain the non-overlapping scope design and link to the architecture docs.

### 7. YouTube Demo (2-3 min)

**Script outline:**
1. (0:00) Problem: "What if 6 AI experts reviewed every commit?"
2. (0:20) Show the pipeline diagram
3. (0:40) Demo: run `/audit-fix` on a file with known issues
4. (1:00) Show the 6 auditors launching in parallel
5. (1:20) Show consolidation + dedup
6. (1:40) Show auto-fix + verification
7. (2:00) Show the architect gate verdict
8. (2:20) "Three ways to use it" + install instructions

## Post-Launch

- Monitor GitHub issues for first-week feedback
- Respond to every HN/Reddit comment in the first 24 hours
- Write a follow-up post after 1 month with usage stats and lessons learned
- Consider adding more auditor examples (database, accessibility, i18n) based on community requests
