#!/usr/bin/env node
/**
 * Prepend a user-facing entry to the marketing changelog from a release-notes
 * file. Run by .github/workflows/release.yml so the marketing changelog
 * (sites/marketing/data/changelog.json) and the GitHub release body come from
 * the SAME polished notes (release-tools), instead of being hand-maintained and
 * drifting.
 *
 * The "changes" array is the bullet lines from the notes (section headers + the
 * intro sentence dropped) — matching the existing changelog.json schema
 * (version / date / changes / dmgUrl) the marketing site renders.
 *
 * Env:
 *   TAG       release tag, e.g. v0.2.124 (used as the entry version)
 *   DATE      entry date YYYY-MM-DD (default: today, UTC)
 *   DMG_URL   download link for this release's DMG
 * Args:
 *   [notes-file]  default: release-notes.md
 *
 * Exits 0 without writing when there are no notes or no bullet lines, so a
 * maintenance release (no user-facing changes) is a clean no-op.
 */

import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const NOTES = process.argv[2] || 'release-notes.md';
const CHANGELOG = 'sites/marketing/data/changelog.json';

const TAG = process.env.TAG;
const DATE = process.env.DATE || new Date().toISOString().slice(0, 10);
const DMG_URL = process.env.DMG_URL || '';

if (!TAG) {
  console.error('TAG env is required (e.g. v0.2.124).');
  process.exit(1);
}
if (!existsSync(NOTES)) {
  console.log(`No notes file at ${NOTES} — skipping changelog.`);
  process.exit(0);
}

const changes = readFileSync(NOTES, 'utf8')
  .split('\n')
  .map((l) => l.trim())
  .filter((l) => /^[-*]\s+/.test(l))
  .map((l) => l.replace(/^[-*]\s+/, '').trim())
  .filter(Boolean);

if (changes.length === 0) {
  console.log('No bullet changes in the notes — skipping changelog entry.');
  process.exit(0);
}

const entry = { version: TAG, date: DATE, changes, dmgUrl: DMG_URL };

let entries = [];
if (existsSync(CHANGELOG)) {
  try {
    const parsed = JSON.parse(readFileSync(CHANGELOG, 'utf8'));
    if (Array.isArray(parsed)) entries = parsed;
    else console.warn('WARNING: changelog.json is not an array — starting fresh.');
  } catch (err) {
    console.warn(`WARNING: could not parse changelog.json (${err.message}) — starting fresh.`);
  }
}

// Newest-first; replace any existing entry for this version (idempotent re-runs).
entries = entries.filter((e) => e?.version !== TAG);
writeFileSync(CHANGELOG, `${JSON.stringify([entry, ...entries], null, 2)}\n`);
console.log(`changelog: added ${TAG} (${changes.length} changes).`);
