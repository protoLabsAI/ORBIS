"""Agent Client Protocol (ACP) client.

ORBIS acts as an ACP *client*: it launches a local coding agent (protoCLI,
OpenCode, Claude Code, Codex) as a subprocess and drives it by voice over ACP
(JSON-RPC 2.0 on the child's stdio). See ``docs/internal/acp-client-spike.md``.
"""

from acp.client import AcpClient, AcpError

__all__ = ["AcpClient", "AcpError"]
