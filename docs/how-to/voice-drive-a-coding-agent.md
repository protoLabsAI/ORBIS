# Voice-drive a coding agent

ORBIS can launch a terminal coding agent — **protoCLI, OpenCode, Claude Code, or
Codex** — and drive it by voice: *"ask proto to add a test to the payments
module."* The agent reads, edits, and runs code in a directory you choose; ORBIS
narrates what it's doing and speaks the result. Under the hood it's the
[Agent Client Protocol](https://agentclientprotocol.com) (ACP), wired in as a
delegate type. For the schema, see [Agent reference → Delegates](/reference/agent#acp-coding-agents).

## Before you start

1. **Install the agent** and make sure it's on your `PATH`. Sign it into its own
   model — ORBIS drives it, it brings its own brain.
2. **Pick the directory it owns** — the repo or folder it should read and edit.

Launch commands (each speaks ACP over stdio):

| Agent | Command | Args |
| --- | --- | --- |
| **protoCLI** | `proto` | `--acp` |
| **OpenCode** | `opencode` | `acp` |
| **Claude Code** | `npx` | `@zed-industries/claude-code-acp` |
| **Codex** | `codex-acp` | *(none — install [zed-industries/codex-acp](https://github.com/zed-industries/codex-acp))* |

## Add it

In **Settings → Agent → Delegates → Add delegate**, choose **ACP coding agent**:

- **Name** — lowercase, no spaces (e.g. `proto`). This is what you say and what
  the model uses in `delegate_to`.
- **Description** — be specific about *which code* it owns; the model uses it to
  choose. "My coding agent for the ORBIS repo" beats "a coding agent".
- **Command** / **Args** — from the table above.
- **Workdir** — the directory the agent is responsible for.

Or in [`config/delegates.yaml`](/reference/config):

```yaml
delegates:
  - name: proto
    description: My coding agent for the ORBIS repo — read, edit, run code there.
    type: acp
    command: proto
    args: ["--acp"]
    workdir: ~/dev/ORBIS
```

The **workdir is the unit of identity**: add the same agent against two repos and
you get two scoped delegates — *"ask proto in the ORBIS repo…"* vs *"…in the
marketing repo."*

## Use it

Just talk: *"ask proto what's failing in the build,"* or *"have proto add a
docstring to the license module."* ORBIS launches the agent in its workdir, hands
over the task, narrates its tool calls as it works ("Editing `app.py`", "Running
`pytest`"), and speaks the outcome. Follow-ups continue the same session, so
*"now also update the changelog"* keeps the context.

## Good to know

- **It edits real files.** The agent works directly in its `workdir` — changes
  land on disk. Point it at a repo under version control so you can review and
  revert.
- **Permissions.** When the agent asks to run a tool, ORBIS currently allows it
  so the task can proceed. A confirm-by-voice policy for writes and shell
  commands is on the roadmap.
- **It must be installed.** ORBIS launches what's on your `PATH`; it doesn't
  bundle the agents. If a launch fails, confirm the command runs in your terminal
  first (e.g. `proto --acp`).

## See also

- [Add a delegate](/how-to/add-a-delegate)
- [Agent reference → Delegates](/reference/agent#delegates)
- [The agent model](/explanation/the-agent-model)
