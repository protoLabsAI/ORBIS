---
name: commit-and-beta-release
description: Commit pending changes, bump the patch version tag, and publish a beta release
---

# /commit-and-beta-release

## When to use
Use when work is ready to ship and needs to be committed, versioned, and published as a beta release. Covers the full sequence from dirty working tree to a tagged, published release.

## Steps
1. Review unstaged/staged changes (`git status`, `git diff`) to confirm what's going out
2. Stage and commit all relevant changes with a descriptive commit message
3. Check recent tags (`git tag --sort=-v:refname | head`) to determine the next version
4. Bump the patch version (or minor if warranted) and create an annotated tag (e.g. `git tag -a v0.1.41 -m "v0.1.41"`)
5. Push commits and tags to origin (`git push && git push --tags`)
6. Create or update the GitHub (or platform) beta release — mark as pre-release, attach release notes summarising changes since last tag
7. Confirm the release is visible and the tag resolves correctly

## Examples
- "commit and publish as needed. lets get our work up to date and ready to publish a beta release" → commit orb-tools removal, tag v0.1.41, publish pre-release on GitHub
- "ship what we have as a beta" → stash check, commit, auto-increment patch tag, push, draft GitHub pre-release with changelog
