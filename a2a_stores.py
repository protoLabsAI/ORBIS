"""Durable A2A stores + push-callback SSRF guard for the a2a-sdk wiring.

a2a-sdk owns the task lifecycle and push-config persistence, but its
``DefaultRequestHandler`` defaults to the *in-memory* ``InMemoryTaskStore`` /
``InMemoryPushNotificationConfigStore`` — task and push state are lost on a
restart. This module restores the two capabilities the bespoke
``a2a_task_store.py`` / ``a2a_push_store.py`` provided before the SDK migration:

1. **Durable persistence** — SQLite-backed ``DatabaseTaskStore`` /
   ``DatabasePushNotificationConfigStore`` (via SQLAlchemy + aiosqlite), at the
   same on-disk paths the bespoke stores used (instance-scoped per ADR 0004,
   ``/sandbox`` → ``~/.protoagent`` fallback). The SDK DB stores expose no TTL
   knob; the task store carries a ``last_updated`` column, so a 24h TTL sweep is
   reimplemented here (``sweep_expired_tasks``). The push-config model has no
   timestamp column, so push configs persist without the prior 24h TTL.

2. **SSRF guard** — a client supplies the push-notification callback URL; the
   SDK's ``BasePushNotificationSender`` POSTs to it with no validation hook. The
   bespoke store rejected loopback / RFC1918 / link-local / multicast / reserved
   targets (with a hostname + CIDR allowlist for trusted docker-network agents).
   That policy is restored verbatim and applied at BOTH config set-time
   (``ValidatingPushNotificationConfigStore.set_info``) and send-time
   (``ValidatingPushNotificationSender._dispatch_notification``).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from a2a.server.context import ServerCallContext
from a2a.server.models import TaskModel
from a2a.server.tasks import (
    BasePushNotificationSender,
    DatabasePushNotificationConfigStore,
    DatabaseTaskStore,
)
from a2a.server.tasks.push_notification_sender import PushNotificationEvent
from a2a.types import TaskPushNotificationConfig

log = logging.getLogger(__name__)

_DEFAULT_TTL_S = 24 * 60 * 60  # 24h, matching the bespoke stores


# ── SSRF guard for push-notification callback URLs ──────────────────────────────


def _parse_allowlist() -> tuple[frozenset[str], tuple]:
    """Parse the webhook allowlist env vars once per import.

    ``PUSH_NOTIFICATION_ALLOWED_HOSTS`` is a comma-separated list of
    hostnames (e.g. ``workstacean,automaker-server``) that bypass the
    SSRF check entirely — trusted internal agents on the docker
    network where every hostname resolves to an RFC1918 address by
    design.

    ``PUSH_NOTIFICATION_ALLOWED_CIDRS`` is a comma-separated list of
    CIDR ranges (e.g. ``10.0.14.0/24``) that bypass the SSRF check
    when the resolved IP falls inside any of them.

    Both are empty by default — the guard stays default-deny for any
    caller the operator hasn't explicitly trusted.
    """
    hosts_raw = os.environ.get("PUSH_NOTIFICATION_ALLOWED_HOSTS", "")
    cidrs_raw = os.environ.get("PUSH_NOTIFICATION_ALLOWED_CIDRS", "")
    hosts = frozenset(h.strip() for h in hosts_raw.split(",") if h.strip())
    cidrs = []
    for c in cidrs_raw.split(","):
        c = c.strip()
        if not c:
            continue
        try:
            cidrs.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            log.warning("[a2a] ignoring malformed CIDR in allowlist: %s", c)
    return hosts, tuple(cidrs)


def is_safe_webhook_url(url: str) -> bool:
    """Reject unsafe webhook targets before we accept or fire a push config.

    Defends against SSRF: a client supplying http://169.254.169.254/... or
    http://10.0.0.1/... as a webhook would have the agent POST task payloads to
    internal cloud metadata, adjacent private services, or the loopback
    device. One-time resolution is not a full defence against DNS rebinding,
    but it closes the trivial "just give it a RFC1918 literal" vector.

    Accepts:
    - http/https URLs to globally-routable IPs.
    - Hostnames in ``PUSH_NOTIFICATION_ALLOWED_HOSTS`` (trusted docker-network
      agents that resolve to RFC1918 by design).
    - Resolved IPs falling inside ``PUSH_NOTIFICATION_ALLOWED_CIDRS``.

    Rejects: non-http(s) schemes, unresolvable hostnames, and anything that
    resolves to loopback / link-local / private / multicast / reserved
    addresses that isn't explicitly allowlisted.

    The allowlist is re-read on each call so an operator can widen trust via
    env without a restart (and so tests can flip it with monkeypatch).
    """
    allowed_hosts, allowed_cidrs = _parse_allowlist()

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False

    # Hostname allowlist takes precedence — trusted docker-network agents
    # where the DNS name resolves to an RFC1918 address by design.
    if host in allowed_hosts:
        return True

    # If the hostname is already a literal IP, check it directly; otherwise
    # resolve once and check every returned address (multi-A / AAAA).
    candidates: list[str] = []
    try:
        ipaddress.ip_address(host)
        candidates = [host]
    except ValueError:
        try:
            # getaddrinfo returns (family, type, proto, canonname, sockaddr);
            # sockaddr[0] is the IP for both AF_INET and AF_INET6.
            candidates = [info[4][0] for info in socket.getaddrinfo(host, None)]
        except socket.gaierror:
            return False

    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if allowed_cidrs and any(ip in cidr for cidr in allowed_cidrs):
            continue  # CIDR allowlist bypass — trust this address
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


# ── Validating wrappers around the SDK push surfaces ────────────────────────────


class ValidatingPushNotificationConfigStore(DatabasePushNotificationConfigStore):
    """Durable push-config store that rejects unsafe callback URLs at set-time.

    Set-time validation gives the caller a synchronous failure (the
    ``set`` JSON-RPC call raises) instead of silently dropping the
    notification later. Send-time validation in
    ``ValidatingPushNotificationSender`` is the defence-in-depth backstop.
    """

    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        url = notification_config.url
        if url and not is_safe_webhook_url(url):
            log.warning("[a2a] rejected unsafe webhook url at set-time: %s", url)
            raise ValueError(
                f"push-notification callback url is not allowed: {url!r} "
                "(resolves to loopback/private/link-local/multicast/reserved "
                "and is not allowlisted)"
            )
        await super().set_info(task_id, notification_config, context)


class ValidatingPushNotificationSender(BasePushNotificationSender):
    """Push sender that re-validates the callback URL before each POST.

    Backstops the set-time guard: even if a config slipped in (e.g. written
    directly to the store, or a DNS record that changed since set-time), the
    actual outbound POST is gated on the SSRF policy.
    """

    async def _dispatch_notification(
        self,
        event: PushNotificationEvent,
        push_info: TaskPushNotificationConfig,
        task_id: str,
    ) -> bool:
        url = push_info.url
        if url and not is_safe_webhook_url(url):
            log.warning(
                "[a2a] refusing push delivery to unsafe webhook url for "
                "task_id=%s: %s",
                task_id,
                url,
            )
            return False
        return await super()._dispatch_notification(event, push_info, task_id)


# ── Durable store construction (paths match the bespoke stores) ─────────────────


def _resolve_db_path(leaf: str) -> str:
    """Resolve a writable SQLite path for ``leaf`` (e.g. ``a2a-tasks.db``) under
    ORBIS's data dir, beside the main DB
    (``~/Library/Application Support/orbis/a2a/<leaf>``). ORBIS is single-owner /
    single-instance, so no per-instance scoping is needed."""
    from agent.paths import get_db_path

    path = get_db_path().parent / "a2a" / leaf
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def make_sqlite_engine(db_path: str) -> AsyncEngine:
    """Async SQLAlchemy engine for a local SQLite file (aiosqlite driver)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}")


def build_a2a_stores() -> tuple[
    DatabaseTaskStore,
    ValidatingPushNotificationConfigStore,
    str,
    str,
]:
    """Build the durable task + push-config stores at their on-disk paths.

    Returns ``(task_store, push_config_store, task_db_path, push_db_path)``.
    Each store gets its own engine/file (same split the bespoke stores used:
    ``a2a-tasks.db`` and ``a2a-push.db``). The SDK stores lazy-init their schema
    on first use; ``initialize_a2a_stores`` forces that + a TTL sweep at boot.
    """
    task_db = _resolve_db_path("a2a-tasks.db")
    push_db = _resolve_db_path("a2a-push.db")
    task_store = DatabaseTaskStore(make_sqlite_engine(task_db))
    push_store = ValidatingPushNotificationConfigStore(make_sqlite_engine(push_db))
    return task_store, push_store, task_db, push_db


def build_push_sender(
    push_config_store: ValidatingPushNotificationConfigStore,
    httpx_client: httpx.AsyncClient,
) -> ValidatingPushNotificationSender:
    """SSRF-guarded push sender wired to the durable config store."""
    return ValidatingPushNotificationSender(httpx_client, push_config_store)


async def sweep_expired_tasks(
    engine: AsyncEngine, *, ttl_s: int = _DEFAULT_TTL_S, now: datetime | None = None
) -> int:
    """Delete task rows older than ``ttl_s`` (24h default), keyed on the SDK's
    ``last_updated`` column. The SDK DB store has no TTL knob, so this restores
    the bespoke store's 24h eviction. Returns the number of rows deleted."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=ttl_s)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        result = await session.execute(
            delete(TaskModel).where(TaskModel.last_updated < cutoff)
        )
        await session.commit()
        return result.rowcount or 0


async def initialize_a2a_stores(
    task_store: DatabaseTaskStore,
    push_store: ValidatingPushNotificationConfigStore,
) -> None:
    """Create the schemas + run the task TTL sweep at boot.

    The bespoke task store also failed any non-terminal task on restart (its
    LangGraph runner doesn't survive). The SDK store doesn't expose a state
    index for that, and stale non-terminal rows age out via the 24h sweep, so
    we run the sweep here and leave the rest to the SDK's own task lifecycle.
    """
    await task_store.initialize()
    await push_store.initialize()
    try:
        n = await sweep_expired_tasks(task_store.engine)
        if n:
            log.info("[a2a] swept %d expired task record(s) (24h TTL)", n)
    except Exception:
        log.exception("[a2a] task TTL sweep failed; continuing")

