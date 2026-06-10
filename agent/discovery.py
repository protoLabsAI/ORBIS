"""A2A agent discovery — find protoLabs agents broadcasting on the network.

Ported from protoAgent's fleet discovery (ADR 0042 §I, protoAgent #805/#815/#816)
so ORBIS sees the same `_protoagent._tcp` mDNS broadcasts the fleet uses. Three
channels, merged:

  • **Local port-scan** — probe ``127.0.0.1`` over the fleet port range for
    ``/.well-known/agent-card.json`` (a co-located protoAgent / Roxy / hub).
  • **mDNS / Bonjour** — every protoAgent advertises a ``_protoagent._tcp``
    service on boot; we browse the LAN for them. ORBIS advertises itself the
    same way (when bound non-loopback) so the fleet sees ORBIS too.
  • **Tailnet port-scan** — mDNS is link-local multicast and never crosses a
    Tailscale overlay, so tailnet agents are found by asking the local
    ``tailscale`` CLI for online peers and probing their agent-cards over the
    same port range. (A machine on both the LAN and the tailnet can surface
    twice — LAN IP via mDNS, ``100.x`` via this channel; both URLs are real,
    we don't guess which one to keep.)

Pure discovery: returns reachable agents (name + description + url). Turning
one into a delegate (``type: a2a``, url + ``/a2a``) is the Delegates settings
panel's job. All best-effort — a discovery hiccup never blocks the server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess

import httpx

logger = logging.getLogger(__name__)

# Same service type protoAgent advertises/browses — interop is the point.
_SERVICE_TYPE = "_protoagent._tcp.local."
# protoAgent's fleet port range; ORBIS's own default (7866) sits inside it.
PORT_RANGE = (7860, 7910)

_zc = None  # the advertised Zeroconf instance (None until advertise())
_info = None


def _local_ip() -> str:
    """Best-effort LAN IP (the address other machines reach us on); 127.0.0.1 if offline."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet leaves; just selects the egress interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ── mDNS advertise (wired into lifespan) ──────────────────────────────────────
def advertise(name: str, port: int) -> None:
    """Announce this agent on mDNS so fleet siblings can discover it. Idempotent
    + best-effort.

    Sync zeroconf — call via ``asyncio.to_thread`` from async code (the
    ``_browse_mdns`` convention): constructed on a running event loop it
    attaches to that loop, and ``register_service`` then blocks the same loop
    waiting on its own future — a ~10s ``EventLoopBlocked`` stall at boot
    (protoAgent #815). Guarded below so a regressed call site logs and bails
    instantly instead.
    """
    global _zc, _info
    if _zc is not None or not port:
        return
    if _on_event_loop():
        logger.warning(
            "[discovery] advertise() called on an event loop thread — refusing "
            "(sync zeroconf would deadlock it); call via asyncio.to_thread")
        return
    try:
        from zeroconf import ServiceInfo, Zeroconf

        ip = _local_ip()
        _info = ServiceInfo(
            _SERVICE_TYPE,
            f"{name}.{_SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=int(port),
            properties={"name": name, "v": "1"},
        )
        _zc = Zeroconf()
        _zc.register_service(_info)
        logger.info(f"[discovery] advertising {name} on mDNS ({ip}:{port})")
    except Exception:  # noqa: BLE001 — never block boot on mDNS
        logger.exception("[discovery] mDNS advertise failed — continuing without it")
        _zc = None


def _on_event_loop() -> bool:
    """True when the current thread is running an asyncio loop — where sync zeroconf
    calls (register/unregister/close all wait on loop-scheduled futures) would deadlock."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def stop_advertise() -> None:
    """Withdraw the advertisement. Sync zeroconf — call via ``asyncio.to_thread``
    (see ``advertise``); ``unregister``/``close`` block on the loop the same way."""
    global _zc, _info
    if _zc is not None and _on_event_loop():
        logger.warning(
            "[discovery] stop_advertise() called on an event loop thread — refusing "
            "(sync zeroconf would deadlock it); call via asyncio.to_thread")
        return
    try:
        if _zc is not None:
            if _info is not None:
                _zc.unregister_service(_info)
            _zc.close()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _zc, _info = None, None


# ── discover ──────────────────────────────────────────────────────────────────
async def _probe(client: httpx.AsyncClient, host: str, port: int) -> dict | None:
    """Is there an A2A agent at host:port? Return {name, description, url, host,
    port} from its agent-card. Description rides along because the delegate
    registry picks targets by description — the Add prefill wants it."""
    url = f"http://{host}:{port}"
    try:
        r = await client.get(f"{url}/.well-known/agent-card.json", timeout=1.0)
        if r.status_code != 200:
            return None
        card = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    return {
        "name": card.get("name") or f"{host}:{port}",
        "description": card.get("description") or "",
        "url": url,
        "host": host,
        "port": port,
    }


# macOS App-Store/standalone Tailscale ships the CLI inside the app bundle, not on PATH.
_TAILSCALE_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def _tailscale_cli() -> str | None:
    """The tailscale CLI to ask about the tailnet, or None when not installed."""
    import shutil

    return shutil.which("tailscale") or (
        _TAILSCALE_APP_CLI if os.path.exists(_TAILSCALE_APP_CLI) else None)


def _tailnet_peer_ips(timeout: float = 3.0) -> list[str]:
    """IPv4 tailnet addresses of ONLINE peers (sync subprocess — call via
    ``asyncio.to_thread``). Empty when tailscale isn't installed or isn't up —
    the channel just goes quiet, never errors."""
    cli = _tailscale_cli()
    if not cli:
        return []
    try:
        out = subprocess.run([cli, "status", "--json"], capture_output=True,
                             text=True, timeout=timeout)
        if out.returncode != 0:
            return []
        peers = (json.loads(out.stdout).get("Peer") or {}).values()
        return [ip for p in peers if p.get("Online")
                for ip in (p.get("TailscaleIPs") or []) if "." in ip]
    except Exception:  # noqa: BLE001 — discovery is best-effort, never blocks the endpoint
        logger.debug("[discovery] tailscale status failed", exc_info=True)
        return []


async def _scan_tailnet(port_range: tuple[int, int], known: set) -> list[dict]:
    """Probe every online tailnet peer's agent-card over the fleet port range."""
    ips = await asyncio.to_thread(_tailnet_peer_ips)
    if not ips:
        return []
    async with httpx.AsyncClient() as client:
        tasks = [_probe(client, ip, p) for ip in ips
                 for p in range(port_range[0], port_range[1] + 1) if (ip, p) not in known]
        return [r for r in await asyncio.gather(*tasks) if r]


async def _scan_local(port_range: tuple[int, int], skip_ports: set[int]) -> list[dict]:
    async with httpx.AsyncClient() as client:
        tasks = [_probe(client, "127.0.0.1", p)
                 for p in range(port_range[0], port_range[1] + 1) if p not in skip_ports]
        return [r for r in await asyncio.gather(*tasks) if r]


def _browse_mdns(timeout: float) -> list[dict]:
    """One-shot LAN browse for `_protoagent._tcp` (sync zeroconf — call via asyncio.to_thread)."""
    found: list[dict] = []
    try:
        import time

        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

        zc = Zeroconf()

        class _L(ServiceListener):
            def add_service(self, zc_, type_, name):
                info = zc_.get_service_info(type_, name, timeout=int(timeout * 1000))
                if not info or not info.addresses:
                    return
                ip = socket.inet_ntoa(info.addresses[0])
                props = info.properties or {}
                nm = (props.get(b"name") or b"").decode() or name.split(".")[0]
                found.append({"name": nm, "description": "",
                              "url": f"http://{ip}:{info.port}",
                              "host": ip, "port": info.port})

            def update_service(self, *a):
                pass

            def remove_service(self, *a):
                pass

        ServiceBrowser(zc, _SERVICE_TYPE, _L())
        time.sleep(timeout)
        zc.close()
    except Exception:  # noqa: BLE001
        logger.exception("[discovery] mDNS browse failed")
    return found


async def discover(*, known: set | None = None,
                   port_range: tuple[int, int] = PORT_RANGE,
                   timeout: float = 1.5) -> list[dict]:
    """A2A agents broadcasting nearby (local + LAN + tailnet) minus the ones
    already configured.

    ``known`` is a set of ``(host, port)`` already known (ORBIS itself + existing
    a2a delegates); those are filtered out. Returns ``[{name, description, url,
    host, port}]`` — duplicates by ``(host, port)`` are collapsed; a dual-homed
    machine (LAN IP via mDNS + ``100.x`` via tailnet) intentionally keeps both
    addresses."""
    known = known or set()
    skip_local = {p for (h, p) in known if h in ("127.0.0.1", "localhost")}
    local, network, tailnet = await asyncio.gather(  # independent channels — scan concurrently
        _scan_local(port_range, skip_local),
        asyncio.to_thread(_browse_mdns, timeout),
        _scan_tailnet(port_range, known),
    )
    out: dict[tuple, dict] = {}
    for a in network + tailnet + local:  # local wins on a clash (it probed the card; mDNS may not have)
        if (a["host"], a["port"]) in known:
            continue
        out[(a["host"], a["port"])] = a

    # mDNS adverts carry only the service name — fetch the card for entries
    # missing a description so the delegate-add prefill is LLM-pickable.
    bare = [a for a in out.values() if not a.get("description")]
    if bare:
        async with httpx.AsyncClient() as client:
            cards = await asyncio.gather(
                *(_probe(client, a["host"], a["port"]) for a in bare))
        for a, card in zip(bare, cards):
            if card:
                a["description"] = card["description"]
                a["name"] = card["name"] or a["name"]
    return list(out.values())
