# CODEX_PROMPTS.md

## 1. Plan only
You are the technical lead for this repository. Read `AGENTS.md`, `PROJECT_PLAN.md`, and all existing docs. Do not write implementation code yet.

Goal: produce a file-level plan for the next approved phase.
Constraints: no automated access to Gaijin Marketplace and no automated trading. Preserve existing architecture. State assumptions instead of silently inventing requirements.
Done when: the plan lists files to create/change, data/API contracts, tests, migration impact, risks, and explicit acceptance criteria.

## 2. Implement one vertical slice
Implement only: <DESCRIBE ONE USER-VISIBLE OR TESTABLE SLICE>.

Context: follow `AGENTS.md` and the approved plan. Inspect existing code before editing.
Constraints: avoid unrelated refactors and new dependencies. Keep money as Decimal and time as UTC. No Gaijin automation.
Done when: relevant tests, lint, type checks, and builds pass; documentation is updated; summarize the diff and remaining risks.

## 3. Diagnose a failure
Investigate this failure without immediately rewriting broad areas: <PASTE ERROR>.

First reproduce it, identify the root cause, and show the smallest high-confidence fix. Add a regression test. Run the relevant checks and report exact results.

## 4. Review current changes
Review the current branch against `main`. Do not modify files first.

Focus on compliance, future-data leakage, money/fees/time zones, security and secrets, import idempotency, migrations, API compatibility, missing tests, and user-visible failure states. Report findings by severity with file/line evidence and a concrete fix.

## 5. Retrospective and AGENTS update
Analyze mistakes or repeated corrections from this task. Propose the smallest practical update to `AGENTS.md` that would prevent recurrence. Do not add vague rules. Show the proposed diff before applying it.
