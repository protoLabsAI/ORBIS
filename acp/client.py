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
import json
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

# ACP protocol version ORBIS speaks. Negotiated in `initialize`.
PROTOCOL_VERSION = 1


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
        self._start_lock = asyncio.Lock()

        # Per-turn state (one turn at a time).
        self._answer = ""
        self._progress: ProgressCallback | None = None

    # -- lifecycle -----------------------------------------------------------

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            await self._start()

    async def _start(self) -> None:
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
                env={**os.environ, **(self.env or {})},
            )
        except FileNotFoundError as exc:
            raise AcpError(
                f"agent binary not found: {self.command!r} "
                "(is it installed and on PATH?)"
            ) from exc

        self._reader_task = asyncio.create_task(self._read_loop())
        asyncio.create_task(self._drain_stderr())
        await self._initialize()
        await self._new_session()
        logger.info(
            "[acp/%s] up (pid=%s, session=%s, cwd=%s)",
            self.name,
            self._proc.pid,
            self._session_id,
            self.cwd,
        )

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass

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
            option_id = self._auto_allow(msg.get("params") or {})
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

    # -- handshake -----------------------------------------------------------

    async def _initialize(self) -> None:
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
        try:
            result = await self._request(
                "session/prompt",
                {
                    "sessionId": self._session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
                timeout=timeout,
            )
        finally:
            self._progress = None
        stop = (result or {}).get("stopReason")
        logger.info("[acp/%s] turn complete (stopReason=%s)", self.name, stop)
        return self._answer.strip()
