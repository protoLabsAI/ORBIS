"""A2A network discovery (agent/discovery.py) + GET /api/delegates/discover.

The module is a port of protoAgent's fleet discovery (ADR 0042 §I); these tests
cover the ORBIS-side contract:

  - advertise/stop_advertise lifecycle + the event-loop guard (sync zeroconf
    on a running loop blocks it ~10s at boot — protoAgent #815)
  - discover() merges channels, filters the known set, dedupes by (host, port),
    and enriches description-less mDNS finds from their agent card
  - the endpoint excludes already-configured a2a delegates + ORBIS itself
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
import yaml
from fastapi.testclient import TestClient

import agent.delegate_config_store as store
import app as app_module
from agent import discovery
from app import app


@pytest.fixture(autouse=True)
def _reset_zc(monkeypatch):
    monkeypatch.setattr(discovery, "_zc", None)
    monkeypatch.setattr(discovery, "_info", None)


# ---------------------------------------------------------------------------
# advertise lifecycle
# ---------------------------------------------------------------------------


class _FakeZeroconf:
    def __init__(self):
        self.registered = None
        self.closed = False

    def register_service(self, info):
        self.registered = info

    def unregister_service(self, info):
        self.registered = None

    def close(self):
        self.closed = True


@pytest.fixture
def fake_zeroconf(monkeypatch):
    mod = types.ModuleType("zeroconf")
    mod.Zeroconf = _FakeZeroconf
    mod.ServiceInfo = lambda *a, **kw: {"args": a, "kw": kw}
    monkeypatch.setitem(sys.modules, "zeroconf", mod)
    return mod


def test_advertise_registers_off_loop(fake_zeroconf):
    discovery.advertise("orbis", 7866)
    assert isinstance(discovery._zc, _FakeZeroconf)
    assert discovery._zc.registered is not None

    discovery.advertise("orbis", 7866)  # idempotent — second call is a no-op
    discovery.stop_advertise()
    assert discovery._zc is None and discovery._info is None


def test_advertise_refuses_on_event_loop(fake_zeroconf, caplog):
    async def _on_loop():
        discovery.advertise("orbis", 7866)

    asyncio.run(_on_loop())
    assert discovery._zc is None  # bailed before touching zeroconf
    assert "asyncio.to_thread" in caplog.text


def test_stop_advertise_refuses_on_event_loop(caplog):
    zc = _FakeZeroconf()
    discovery._zc = zc

    async def _on_loop():
        discovery.stop_advertise()

    asyncio.run(_on_loop())
    assert discovery._zc is zc and not zc.closed  # untouched — refused, not deadlocked
    assert "asyncio.to_thread" in caplog.text

    discovery.stop_advertise()  # off the loop: cleans up


# ---------------------------------------------------------------------------
# discover() — channel merge, known filter, dedupe, description enrichment
# ---------------------------------------------------------------------------


def _agent(host, port, name, description=""):
    return {"name": name, "description": description,
            "url": f"http://{host}:{port}", "host": host, "port": port}


@pytest.fixture
def patched_channels(monkeypatch):
    """Stub the three channels + the card-probe enrichment pass."""
    state = {
        "local": [], "mdns": [], "tailnet": [],
        "cards": {},  # (host, port) -> card dict for the enrichment probe
    }

    async def _scan_local(port_range, skip_ports):
        return [a for a in state["local"] if a["port"] not in skip_ports]

    def _browse_mdns(timeout):
        return state["mdns"]

    async def _scan_tailnet(port_range, known):
        return [a for a in state["tailnet"] if (a["host"], a["port"]) not in known]

    async def _probe(client, host, port):
        return state["cards"].get((host, port))

    monkeypatch.setattr(discovery, "_scan_local", _scan_local)
    monkeypatch.setattr(discovery, "_browse_mdns", _browse_mdns)
    monkeypatch.setattr(discovery, "_scan_tailnet", _scan_tailnet)
    monkeypatch.setattr(discovery, "_probe", _probe)
    # Neutral own-IP so the co-located normalization (protoAgent #837) never
    # accidentally fires on the dev machine's real LAN address.
    monkeypatch.setattr(discovery, "_local_ip", lambda: "10.255.255.1")
    return state


def test_discover_filters_known_and_dedupes(patched_channels):
    patched_channels["local"] = [_agent("127.0.0.1", 7870, "roxy", "ops agent")]
    patched_channels["mdns"] = [
        _agent("192.168.1.20", 7871, "proto", "dev agent"),
        _agent("192.168.1.21", 7872, "known-one", "already configured"),
    ]
    # Dual report of the same (host, port) — local wins, no duplicate.
    patched_channels["tailnet"] = [_agent("127.0.0.1", 7870, "roxy-tailnet")]

    found = asyncio.run(discovery.discover(known={("192.168.1.21", 7872)}))
    by_key = {(a["host"], a["port"]): a for a in found}
    assert set(by_key) == {("127.0.0.1", 7870), ("192.168.1.20", 7871)}
    assert by_key[("127.0.0.1", 7870)]["name"] == "roxy"  # local won the clash


def test_discover_collapses_colocated_mdns_with_local_scan(
    patched_channels, monkeypatch,
):
    """A co-located agent's mDNS advert carries the machine's LAN IP — without
    normalization it surfaces twice (loopback via local scan + LAN IP via mDNS,
    different (host, port) keys). protoAgent #837."""
    monkeypatch.setattr(discovery, "_local_ip", lambda: "192.168.5.31")
    patched_channels["local"] = [_agent("127.0.0.1", 7874, "roxy", "ops agent")]
    patched_channels["mdns"] = [
        _agent("192.168.5.31", 7874, "roxy"),  # same agent, own-IP advert
        _agent("192.168.1.20", 7871, "proto", "dev agent"),  # genuinely remote
    ]

    found = asyncio.run(discovery.discover())
    by_key = {(a["host"], a["port"]): a for a in found}
    assert set(by_key) == {("127.0.0.1", 7874), ("192.168.1.20", 7871)}
    assert by_key[("127.0.0.1", 7874)]["description"] == "ops agent"  # local won


def test_discover_own_ip_advert_hits_known_exclusion(patched_channels, monkeypatch):
    """A loopback-configured delegate's own mDNS advert (LAN IP) must collapse
    into the known set, not reappear as 'discovered'."""
    monkeypatch.setattr(discovery, "_local_ip", lambda: "192.168.5.31")
    patched_channels["mdns"] = [_agent("192.168.5.31", 7874, "roxy")]

    found = asyncio.run(discovery.discover(known={("127.0.0.1", 7874)}))
    assert found == []


def test_discover_enriches_mdns_description_from_card(patched_channels):
    patched_channels["mdns"] = [_agent("192.168.1.30", 7875, "quinn")]  # no description
    patched_channels["cards"][("192.168.1.30", 7875)] = _agent(
        "192.168.1.30", 7875, "quinn", "QA engineer — release verification")

    found = asyncio.run(discovery.discover())
    assert found == [_agent("192.168.1.30", 7875, "quinn",
                            "QA engineer — release verification")]


# ---------------------------------------------------------------------------
# GET /api/delegates/discover — known-set assembly
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_delegates_yaml(tmp_path, monkeypatch):
    p = tmp_path / "delegates.yaml"
    monkeypatch.setattr(store, "DEFAULT_PATH", str(p))
    from agent.delegates import DelegateRegistry
    monkeypatch.setattr(app_module, "_DELEGATES", DelegateRegistry(p))
    return p


def test_discover_endpoint_excludes_configured_and_self(
    isolated_delegates_yaml, monkeypatch,
):
    isolated_delegates_yaml.write_text(yaml.safe_dump({"delegates": [
        {"name": "ava", "description": "fleet", "type": "a2a",
         "url": "http://192.168.1.50:7860/a2a"},
        {"name": "opus", "description": "llm", "type": "openai",
         "url": "http://gateway:4000/v1", "model": "m"},
    ]}))
    app_module._DELEGATES.reload()
    monkeypatch.setenv("ORBIS_BOUND_PORT", "7866")
    monkeypatch.setattr(discovery, "_local_ip", lambda: "192.168.1.10")

    seen: dict = {}

    async def _fake_discover(*, known=None, **kw):
        seen["known"] = known
        return [_agent("192.168.1.60", 7861, "proto", "dev agent")]

    monkeypatch.setattr(discovery, "discover", _fake_discover)

    r = TestClient(app).get("/api/delegates/discover")
    assert r.status_code == 200
    assert r.json() == {"discovered": [
        _agent("192.168.1.60", 7861, "proto", "dev agent")]}
    # a2a delegate's endpoint + both self addresses; the openai entry is not
    # an a2a card host and stays out.
    assert seen["known"] == {
        ("192.168.1.50", 7860), ("127.0.0.1", 7866), ("192.168.1.10", 7866)}
