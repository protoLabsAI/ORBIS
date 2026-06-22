"""ACP client — launch a coding agent and drive one session by voice.

ORBIS is the ACP *client*. One ``AcpClient`` owns one agent subprocess and one
session, cached per delegate so follow-up turns continue the thread (mirrors
``A2AClient``'s sticky ``contextId``). Transport is JSON-RPC 2.0, newline-
delimited, over the child's stdin/stdout. Spec: https://agentclientprotocol.com.

PR1 scope (the thin vertical from the spike):
  * handshake: ``initialize`` → ``session/new`` (cwd = the delegate's workdir)
  * one turn: ``session/prompt`` → accumulate ``agent_message_chunk`` text as the
    answer; narrate ``tool_call`` titles via ``progress_callback`` ("Editing
    app.py", "Running pytest") for live voice feedback
  * auto-allow ``session/request_permission`` (policy + voice-confirm land next)
  * ``fs/*`` and ``terminal/*`` are NOT advertised — the local agent uses its own
    file access, scoped to the session ``cwd``. The client-served workspace
    sandbox is the next PR.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

# ACP protocol version ORBIS speaks. Negotiated in `initialize`.
PROTOCOL_VERSION = 1

# asyncio's StreamReader defaults to a 64 KB line limit; a single newline-
# delimited ACP JSON-RPC message routinely exceeds that (a tool result with a
# file's contents, a large diff). Past the limit `readline()` raises
# LimitOverrunError, which kills the read loop and aborts the turn mid-build.
# Give the reader generous headroom.
_STDOUT_LINE_LIMIT = 32 * 1024 * 1024  # 32 MB

# Env markers Claude Code sets so a nested `claude` can detect "am I already
# running inside another Claude Code session?". If ORBIS's sidecar was itself
# launched from within a Claude Code session (dogfooding), these are inherited —
# and a `claude`/claude-agent-acp delegate we spawn then hits the *"cannot be
# launched inside another Claude Code session"* guard and respawn-loops with no
# surfaced error. Strip them from the ACP launch env: ``CLAUDECODE`` plus the
# whole ``CLAUDE_CODE_*`` family. Harmless for non-Claude agents (proto/codex
# don't read them). ``ANTHROPIC_*`` is left alone — it carries credentials.
_NESTED_CLAUDE_ENV_EXACT = frozenset({"CLAUDECODE"})
_NESTED_CLAUDE_ENV_PREFIX = "CLAUDE_CODE_"


def _launch_env(extra: dict[str, str] | None) -> dict[str, str]:
    """Build the subprocess environment for an ACP agent: the sidecar's own
    ``os.environ`` with the nested-Claude markers stripped (see above), then the
    delegate's ``env`` overlaid last — so an operator who *deliberately* sets one
    of these in the delegate env still wins."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _NESTED_CLAUDE_ENV_EXACT and not k.startswith(_NESTED_CLAUDE_ENV_PREFIX)
    }
    env.update(extra or {})
    return env


class AcpError(Exception):
    """Any ACP transport / protocol failure. The caller speaks the message."""


class AcpClient:
    """Drive a single ACP agent subprocess + session.

    Construct once per delegate and reuse: the process + session persist across
    turns. Not safe for concurrent prompts on one instance (a session is a
    single conversation); callers serialize turns, as ``delegate_to`` /
    ``orchestrate`` already do per delegate.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        name: str = "acp",
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.cwd = str(Path(cwd).expanduser())
        self.env = env
        self.name = name

        self._proc: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()

        # Per-turn state (one turn at a time).
        self._answer = ""
        self._progress: ProgressCallback | None = None

    # -- lifecycle -----------------------------------------------------------

    async def _ensure_started(self) -> None:
        """Start the agent for a real dispatch: spawn + ``initialize`` +
        ``session/new``. Idempotent — a no-op when already up."""
        async with self._start_lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            await self._start()

    async def handshake(self) -> None:
        """Start the agent for a *liveness check only*: spawn + run the ACP
        ``initialize`` round-trip and STOP — no ``session/new``, no session state
        touched. The genuinely cheap, side-effect-free probe the delegate health
        check wants (the static `which` + dir check false-greens a `command` that
        is on PATH but can't actually speak ACP, e.g. `claude` without its ACP
        adapter). The caller ``close()``s it. Idempotent."""
        async with self._start_lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            await self._start(open_session=False)

    async def _start(self, *, open_session: bool = True) -> None:
        if not Path(self.cwd).is_dir():
            raise AcpError(f"workdir does not exist: {self.cwd}")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                cwd=self.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Strip the inherited nested-Claude markers (CLAUDECODE /
                # CLAUDE_CODE_*) so a spawned Claude backend doesn't refuse to
                # launch "inside another Claude Code session"; the delegate's env
                # is overlaid last.
                env=_launch_env(self.env),
                # Put the agent in its OWN session/process group so teardown can
                # kill the WHOLE tree (the adapter *and* the backend it spawns,
                # e.g. `npx …claude-agent-acp` → node). Without this, terminate()
                # signals only the direct child; its backend reparents to init and
                # leaks. POSIX-only (start_new_session ⇒ setsid()).
                start_new_session=True,
                # Raise the per-line buffer ceiling — ACP messages exceed the
                # 64 KB default and would otherwise raise LimitOverrunError.
                limit=_STDOUT_LINE_LIMIT,
            )
        except FileNotFoundError as exc:
            raise AcpError(
                f"agent binary not found: {self.command!r} "
                "(is it installed and on PATH?)"
            ) from exc

        # The subprocess now exists. If the handshake raises OR the caller's
        # wait_for cancels us mid-initialize (a probe timeout), reap the tree we
        # just spawned instead of leaking it — close() is idempotent.
        try:
            self._reader_task = asyncio.create_task(self._read_loop())
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            await self._initialize()
            # The probe path (handshake) stops here — a liveness check must NOT
            # open a session. A real dispatch opens one.
            if open_session:
                await self._new_session()
        except BaseException:
            with contextlib.suppress(Exception):
                await self.close()
            raise
        logger.info(
            "[acp/%s] up (pid=%s, session=%s, cwd=%s)",
            self.name,
            self._proc.pid,
            self._session_id,
            self.cwd,
        )

    @staticmethod
    def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
        """Send ``sig`` to the subprocess's whole process GROUP (the agent plus
        the backend it spawned), falling back to the bare process if the group is
        already gone. Synchronous syscall + swallows ProcessLookup, so it's safe
        from a teardown/cancel path where our coroutines won't run."""
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.send_signal(sig)

    def kill_now(self) -> None:
        """Synchronously SIGKILL the agent's whole process group — no awaits, so
        it's safe from a CancelledError handler where awaiting cleanup would
        itself be cancelled. The zombie is reaped later by ``proc.wait()`` / the
        child watcher. Use this on the hard-stop path (a delegation is cancelled
        or times out); ``close()`` is the graceful one."""
        proc = self._proc
        if proc and proc.returncode is None:
            self._signal_group(proc, signal.SIGKILL)
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()

    async def close(self) -> None:
        """Cancel the I/O tasks and reap the subprocess TREE. Sends a best-effort
        ``session/close`` first (graceful), then SIGTERM→SIGKILL the agent's whole
        PROCESS GROUP — not just the direct child — so the backend it spawned dies
        with it instead of reparenting to init. Crucially ``await``s
        ``proc.wait()`` so the child is reaped while the loop is alive (else the
        transport's ``__del__`` fires "Event loop is closed" after shutdown)."""
        with contextlib.suppress(Exception):
            await self._close_session()
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        proc = self._proc
        if proc and proc.returncode is None:
            self._signal_group(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._signal_group(proc, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await proc.wait()
            except asyncio.CancelledError:
                # We're being torn down — guarantee the tree is dead, then let the
                # cancellation propagate (don't swallow it).
                self._signal_group(proc, signal.SIGKILL)
                raise
        # Close the subprocess transport too, so its pipe transports don't linger
        # to a post-loop-close GC ("Event loop is closed").
        transport = getattr(proc, "_transport", None) if proc else None
        if transport is not None:
            transport.close()

    # -- I/O loops -----------------------------------------------------------

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        async for raw in self._proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                logger.debug("[acp/%s/stderr] %s", self.name, line)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            async for raw in self._proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("[acp/%s] non-JSON line: %.200s", self.name, line)
                    continue
                await self._handle(msg)
        except asyncio.CancelledError:
            raise
        finally:
            # Fail any in-flight requests if the process dies mid-turn.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(AcpError(f"{self.name} agent exited"))
            self._pending.clear()

    async def _handle(self, msg: dict) -> None:
        # 1) Response to one of our outbound requests.
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(AcpError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return
        method = msg.get("method")
        # 2) Inbound request from the agent (has id) — we must respond.
        if method and "id" in msg:
            await self._handle_request(msg)
            return
        # 3) Notification (no id).
        if method == "session/update":
            await self._handle_update(msg.get("params") or {})

    # -- inbound updates + requests -----------------------------------------

    async def _handle_update(self, params: dict) -> None:
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = (update.get("content") or {}).get("text", "")
            if text:
                self._answer += text
        elif kind == "tool_call":
            # Narrate the tool's human title for live voice feedback
            # ("Editing app.py", "Running pytest") — not the answer text.
            title = update.get("title") or update.get("kind") or "working"
            await self._narrate(str(title))

    async def _handle_request(self, msg: dict) -> None:
        method = msg.get("method")
        rid = msg.get("id")
        if method == "session/request_permission":
            params = msg.get("params") or {}
            option_id = self._auto_allow(params)
            # Trace the decision — a permission the resolver can't answer (→
            # cancelled) can leave the agent blocked silently (a prime idle-freeze
            # suspect), so it's worth a log line at INFO.
            kind = str(((params.get("toolCall") or {}).get("kind") or "")).lower()
            logger.info(
                "[acp/%s] request_permission kind=%s → %s",
                self.name,
                kind or "?",
                "selected" if option_id else "cancelled",
            )
            outcome = (
                {"outcome": "selected", "optionId": option_id}
                if option_id
                else {"outcome": "cancelled"}
            )
            await self._respond(rid, {"outcome": outcome})
        else:
            # We didn't advertise fs/terminal; decline anything else cleanly so
            # the agent falls back to its own capabilities instead of hanging.
            await self._respond_error(rid, -32601, f"method not supported: {method}")

    @staticmethod
    def _auto_allow(params: dict) -> str | None:
        """PR1 permission policy: pick the first 'allow' option (else the first
        option). The voice-confirm + deny-policy layer replaces this."""
        options = params.get("options") or []
        for opt in options:
            if str(opt.get("kind", "")).startswith("allow"):
                return opt.get("optionId")
        return options[0].get("optionId") if options else None

    async def _narrate(self, text: str) -> None:
        if self._progress and text:
            try:
                await self._progress(text)
            except Exception as exc:  # progress is best-effort
                logger.warning("[acp/%s] progress_callback raised: %s", self.name, exc)

    # -- JSON-RPC primitives -------------------------------------------------

    async def _send(self, obj: dict) -> None:
        if not (self._proc and self._proc.stdin):
            raise AcpError("agent not started")
        self._proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict, *, timeout: float = 120.0):
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(rid, None)
            raise AcpError(f"{method} timed out after {timeout}s") from exc

    async def _respond(self, rid, result: dict) -> None:
        await self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    async def _respond_error(self, rid, code: int, message: str) -> None:
        await self._send(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
        )

    async def _notify_session(self, method: str) -> None:
        """Fire-and-forget a session lifecycle notification (``session/cancel`` on
        abort, ``session/close`` on teardown). Notifications have no id and no
        response. Best-effort by contract — it runs on abort/teardown paths and
        must never raise: no-op when the process is gone or no session is open,
        and any send error is swallowed."""
        proc = self._proc
        if not (proc and proc.returncode is None and proc.stdin and self._session_id):
            return
        try:
            proc.stdin.write(
                (
                    json.dumps(
                        {"jsonrpc": "2.0", "method": method, "params": {"sessionId": self._session_id}}
                    )
                    + "\n"
                ).encode()
            )
            await proc.stdin.drain()
        except Exception as exc:  # noqa: BLE001 — abort/teardown path is best-effort
            logger.debug("[acp/%s] %s failed (best-effort): %s", self.name, method, exc)

    async def _cancel_session(self) -> None:
        """Tell the agent to abandon the in-flight turn so a reused session isn't
        left mid-generation. Runs on the prompt abort path (timeout / external
        cancel / transport failure)."""
        await self._notify_session("session/cancel")

    async def _close_session(self) -> None:
        """Tell the agent to release the session before the subprocess is reaped —
        the graceful, spec-aligned counterpart to the SIGTERM in ``close()``."""
        await self._notify_session("session/close")

    # -- handshake -----------------------------------------------------------

    async def _initialize(self) -> None:
        result = (
            await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    # PR1: no client-served fs/terminal — the local agent uses its own.
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                },
                timeout=30.0,
            )
            or {}
        )
        # Log the handshake outcome so the ACP round-trip (initialize → session →
        # prompt → permission → result) is traceable from sidecar.log — an idle
        # freeze with no diagnostics is the hard-to-debug failure mode.
        caps = result.get("agentCapabilities") or {}
        logger.info(
            "[acp/%s] initialize OK (protocol v%s, loadSession=%s)",
            self.name,
            result.get("protocolVersion", PROTOCOL_VERSION),
            bool(caps.get("loadSession")),
        )

    async def _new_session(self) -> None:
        result = await self._request(
            "session/new", {"cwd": self.cwd, "mcpServers": []}, timeout=30.0
        )
        self._session_id = (result or {}).get("sessionId")
        if not self._session_id:
            raise AcpError("session/new returned no sessionId")

    # -- public: one turn ----------------------------------------------------

    async def prompt(
        self,
        text: str,
        *,
        progress_callback: ProgressCallback | None = None,
        timeout: float = 240.0,
    ) -> str:
        """Send one user turn; return the agent's accumulated message text.

        Streams ``tool_call`` titles to ``progress_callback`` for narration
        while the agent works. Raises ``AcpError`` on transport/protocol failure.
        """
        await self._ensure_started()
        self._answer = ""
        self._progress = progress_callback
        logger.info(
            "[acp/%s] → session/prompt (session=%s, %d chars, timeout=%ss)",
            self.name,
            self._session_id,
            len(text),
            int(timeout),
        )
        try:
            result = await self._request(
                "session/prompt",
                {
                    "sessionId": self._session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
                timeout=timeout,
            )
        except (AcpError, asyncio.CancelledError):
            # Turn abandoned — internal timeout, external cancel (an orchestrator's
            # wait_for watchdog, a verbal stop), or transport failure. Tell the
            # agent to drain it so the reused session isn't left mid-generation.
            # (The adapter additionally hard-reaps the process group on cancel.)
            await self._cancel_session()
            raise
        finally:
            self._progress = None
        stop = (result or {}).get("stopReason")
        logger.info("[acp/%s] turn complete (stopReason=%s)", self.name, stop)
        return self._answer.strip()
