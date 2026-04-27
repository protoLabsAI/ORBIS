---
name: verified-status-report
description: Generate a project status summary, verify all claims against ground truth, and correct before finalizing
---

# /verified-status-report

## When to use
When producing a status report, handoff document, or current-state summary where factual accuracy matters. Use any time a human or agent will act on the reported figures (test counts, PR states, version numbers, etc.).

## Steps
1. **Draft the report** — Write the initial status summary pulling from STATUS.md, git tags, open PRs, and known state.
2. **Enumerate verifiable claims** — List every concrete, falsifiable claim in the draft (test counts, PR numbers, version strings, dates, file counts, etc.).
3. **Verify each claim against ground truth** — For each claim, run the appropriate command or read the authoritative source:
   - Test counts: `pytest --collect-only -q | tail -1` and `grep -r "^def test_" tests/ | wc -l`
   - Open PRs: `gh pr list --state open`
   - Current version/tag: `git describe --tags`
   - File counts: `find` or `ls` in the relevant directory
4. **Flag mismatches** — Any claim that doesn't match ground truth is marked as incorrect with the correct value noted.
5. **Revise the report** — Replace all incorrect claims with verified values; re-read for internal consistency.
6. **Final output** — Deliver the corrected, verified report.

## Examples

**Example 1 — Test count correction:**
Draft says "131 passing tests". Verification runs `pytest --collect-only -q` and gets 509 collected. Draft is corrected to "509 tests collected by pytest (461 test functions by grep)".

**Example 2 — PR state correction:**
Draft says "PR #30 is open". Verification runs `gh pr list --state open` and finds no open PRs. Draft is corrected to "No open PRs; PR #30 was previously merged."
