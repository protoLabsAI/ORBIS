#!/usr/bin/env node
/**
 * Rewrite raw release commits into polished user-facing release notes
 * via the Anthropic Messages API.
 *
 * Requires: ANTHROPIC_API_KEY environment variable
 *
 * Usage:
 *   node scripts/rewrite-release-notes.mjs [version] [previous-version]
 *
 * Examples:
 *   node scripts/rewrite-release-notes.mjs v0.1.2 v0.1.1
 *   node scripts/rewrite-release-notes.mjs             # auto-detects latest + previous tag
 *
 * Flags:
 *   --post-discord-release   Post the polished notes to DISCORD_RELEASE_WEBHOOK (user-facing)
 *   --post-discord-dev       Post the polished notes to DISCORD_DEV_WEBHOOK (engineering)
 *   --dry-run                Print the prompt without calling Claude (debug)
 *
 * Adapted from protoMaker/ava's scripts/rewrite-release-notes.mjs.
 * Voice tuned for ORBIS — voice-first AI companion.
 */

import { execSync } from 'node:child_process';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function run(cmd) {
  return execSync(cmd, { encoding: 'utf-8' }).trim();
}

function getTags() {
  const tags = run('git tag --sort=-v:refname').split('\n').filter(Boolean);
  if (tags.length < 2) {
    console.error('Need at least 2 tags to compare. Found:', tags.length);
    process.exit(1);
  }
  return { latest: tags[0], previous: tags[1] };
}

function getCommitsBetween(fromTag, toTag) {
  const log = run(`git log ${fromTag}..${toTag} --pretty=format:"%s"`);
  if (!log) return [];
  return log
    .split('\n')
    .map((line) => line.replace(/^"|"$/g, ''))
    .filter(Boolean);
}

// ---------------------------------------------------------------------------
// Prompt
// ---------------------------------------------------------------------------

const SYSTEM_PROMPT = `You are a release notes writer for ORBIS, a voice-first AI companion. An orb that talks back in real time, remembers you across sessions, and delegates heavy reasoning to the user's configured agents.

Voice: Technical, direct, pragmatic. Speak to builders and the single owner running ORBIS on their own machine. No marketing fluff, no AI hype words ("revolutionizing", "game-changing"), no filler.

Rules:
- Write a short intro sentence (what this release is about in one line)
- Group changes into 2-4 themed sections with bold headers (group by what the user cares about, not raw commit categories)
- Voice pipeline (STT / LLM / TTS / latency), orb visualization, memory + personality, and configuration are the four dimensions users care about most
- Each item: one sentence, present tense, explains the user-facing impact
- Skip internal-only changes (CI config, version bumps, merge commits, chore commits) unless they fix a user-visible problem
- Skip "Merge" / "chore: release" / "promote" commits entirely
- If a commit message is unclear, infer the impact from context or omit it
- End with a one-liner on what's next if the commit history suggests ongoing work
- Keep the total output under 300 words
- Use plain markdown: **bold** for section headers, - for bullets
- No emojis
- Do NOT wrap output in code fences — output the markdown directly`;

function buildPrompt(version, previousVersion, commits) {
  const filtered = commits.filter((c) => {
    const lower = c.toLowerCase();
    return (
      !lower.startsWith('merge ') &&
      !lower.startsWith('chore: release') &&
      !lower.startsWith('promote')
    );
  });

  const commitList = filtered.map((c) => `- ${c}`).join('\n');

  return `Rewrite these raw commit messages into user-facing release notes for ${version} (previous: ${previousVersion}).

Raw commits:
${commitList || '(no meaningful commits — write a brief maintenance release note)'}`;
}

// ---------------------------------------------------------------------------
// Claude API call
// ---------------------------------------------------------------------------

async function callClaude(systemPrompt, userPrompt) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    console.error('ANTHROPIC_API_KEY not set.');
    process.exit(1);
  }

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1024,
      system: systemPrompt,
      messages: [{ role: 'user', content: userPrompt }],
    }),
  });

  if (!res.ok) {
    const body = await res.text();
    console.error(`Claude API error: ${res.status} ${body}`);
    process.exit(1);
  }

  const data = await res.json();
  return data.content[0].text;
}

// ---------------------------------------------------------------------------
// Discord posting
// ---------------------------------------------------------------------------

async function postToDiscord(webhookUrl, version, notes, channelName) {
  if (!webhookUrl) {
    console.error(`${channelName} webhook not set. Skipping.`);
    return false;
  }

  const releaseUrl = `https://github.com/protoLabsAI/ORBIS/releases/tag/${version}`;
  // Discord embed description caps at 4096; leave headroom for the url line.
  const truncated = notes.length > 3900 ? notes.slice(0, 3900) + '\n...' : notes;

  const payload = {
    embeds: [
      {
        title: `ORBIS ${version}`,
        url: releaseUrl,
        description: truncated,
        color: 0xf59e0b, // amber — matches the ORBIS accent
      },
    ],
  };

  const res = await fetch(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    console.error(`${channelName} webhook failed: ${res.status} ${res.statusText} ${body}`);
    return false;
  }

  console.log(`Posted to ${channelName}`);
  return true;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const args = process.argv.slice(2);
const flags = args.filter((a) => a.startsWith('--'));
const positional = args.filter((a) => !a.startsWith('--'));

const dryRun = flags.includes('--dry-run');
const postRelease = flags.includes('--post-discord-release');
const postDev = flags.includes('--post-discord-dev');

// Resolve versions
let version, previousVersion;
if (positional.length >= 2) {
  version = positional[0];
  previousVersion = positional[1];
} else {
  const tags = getTags();
  version = positional[0] || tags.latest;
  previousVersion = positional[1] || tags.previous;
}

console.log(`Generating release notes: ${previousVersion} -> ${version}`);

const commits = getCommitsBetween(previousVersion, version);
console.log(`Found ${commits.length} commits\n`);

const userPrompt = buildPrompt(version, previousVersion, commits);

if (dryRun) {
  console.log('=== SYSTEM PROMPT ===');
  console.log(SYSTEM_PROMPT);
  console.log('\n=== USER PROMPT ===');
  console.log(userPrompt);
  process.exit(0);
}

console.log('Calling Claude API (haiku-4-5)...\n');
const notes = await callClaude(SYSTEM_PROMPT, userPrompt);

console.log('=== RELEASE NOTES ===');
console.log(notes);
console.log('=====================\n');

if (postRelease) {
  await postToDiscord(process.env.DISCORD_RELEASE_WEBHOOK, version, notes, '#release');
}
if (postDev) {
  await postToDiscord(process.env.DISCORD_DEV_WEBHOOK, version, notes, '#dev');
}
