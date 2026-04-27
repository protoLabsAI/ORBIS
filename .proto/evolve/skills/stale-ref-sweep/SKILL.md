---
name: stale-ref-sweep
description: Audit and fix stale cross-document references after a feature removal or rename
---

# /stale-ref-sweep

## When to use
After removing, renaming, or significantly changing a feature/tool/concept, use this to systematically find and fix every doc or source file that still references the old thing.

## Steps
1. Identify the removed/renamed item (e.g. a tool name, function, risk ID, config key).
2. Grep all relevant files (docs, source, config) for the stale reference string.
3. For each hit, read the surrounding context (check the specific line range) to understand the nature of the reference.
4. Fix each stale reference: update text, remove the stale entry, or replace with the current equivalent.
5. Re-check the same files to confirm no references remain.
6. Repeat for any secondary references discovered during fixing (e.g. risk IDs that became moot, aliases, related terms).

## Examples
- Removing `apply_palette` tool: grep all .md and .py files for `apply_palette`, read context at each hit, update or remove the stale mentions across RISKS.md, RESEARCH.md, STATUS.md, HANDOFF.md, and app.py.
- Renaming an orb-mod subsystem: find every doc that calls it by the old name, patch each one, verify clean grep output at the end.
