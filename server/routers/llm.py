"""LLM test / model-management / local-detect routes — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter

from agent.persona import get_active_persona
from fastapi.responses import JSONResponse
from app import _resolve_skill_llm


router = APIRouter()

# Kept as a local literal (not imported from voice.llm.oauth) so this module —
# imported at app startup — never pulls the OAuth/provider stack until a route
# actually needs it.
_NATIVE_OAUTH_PROVIDERS = frozenset({"anthropic-oauth", "openai-codex"})


def _llm_probe_url_is_safe(url: str) -> bool:
    """SSRF guard for the unauth ``/api/llm/test`` + ``/api/llm/models``
    routes.

    Unlike ``_ollama_url_is_safe`` (which constrains to local/private —
    correct for the ``ollama pull`` route), these routes must reach
    *public* providers (OpenAI, OpenRouter, …) as well as a local Ollama,
    so we can't allow-list to private space. Instead we block only the
    genuinely dangerous targets: the cloud-metadata link-local range
    (169.254.0.0/16 / fe80::/10), the unspecified address, and multicast.
    Hostnames are resolved so ``http://meta.attacker.com`` that points at
    169.254.169.254 is also rejected. Everything else (public + private +
    loopback) is allowed because those are all legitimate LLM endpoints.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False

    def _ip_is_dangerous(ip: ipaddress._BaseAddress) -> bool:
        return ip.is_link_local or ip.is_unspecified or ip.is_multicast

    try:
        return not _ip_is_dangerous(ipaddress.ip_address(host))
    except ValueError:
        pass  # hostname — resolve and check every answer
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except OSError:
        # DNS failure — let the probe itself surface the connection error.
        return True
    for info in infos:
        addr = info[4][0]
        try:
            if _ip_is_dangerous(ipaddress.ip_address(addr)):
                return False
        except ValueError:
            continue
    return True


@router.post("/api/llm/test")
async def llm_test(body: dict):
    """Real round-trip ping against a configured LLM endpoint.

    Body: ``{url, model, api_key?}``. Returns ``{ok, latency_ms?,
    error?, status?}``. Unauth on purpose — the setup wizard may run
    before the owner API key is set, and the user's LLM credentials
    are what's really being validated here, not their ORBIS auth.
    """
    from agent.llm_probe import ping_endpoint
    provider = str(body.get("provider") or "").strip().lower()
    if provider in _NATIVE_OAUTH_PROVIDERS:
        # Subscription providers authenticate from the OAuth credential store
        # and speak their own wire protocol — url/api_key don't apply.
        from voice.llm.oauth_discovery import validate_oauth_connection
        ok, error = await asyncio.to_thread(
            validate_oauth_connection, provider, str(body.get("model") or "")
        )
        return {"ok": ok} if ok else {"ok": False, "error": error}
    url = str(body.get("url") or "")
    # Off-loop: the safety check resolves DNS (blocking getaddrinfo).
    if url and not await asyncio.to_thread(_llm_probe_url_is_safe, url):
        return {"ok": False, "error": "URL not allowed (blocked target)"}
    api_key = str(body.get("api_key") or "")
    # "Leave blank to keep the saved key" must apply to the test too —
    # otherwise a blank field reports a false 'auth rejected' even when a
    # valid key is saved. Fall back to the resolved key, but only for the
    # already-configured URL so we never leak it to a different provider.
    if not api_key:
        try:
            saved = _resolve_skill_llm(get_active_persona())
            if not url or url == saved.get("url"):
                api_key = saved.get("api_key") or ""
        except Exception:
            pass
    return await ping_endpoint(
        url=url,
        model=str(body.get("model") or ""),
        api_key=api_key,
    )


@router.post("/api/llm/models")
async def llm_models(body: dict):
    """GET /models against a configured URL + API key. Returns
    ``{ok, models[], error?}``. Populates the wizard's model combobox.

    Unauth, same rationale as /api/llm/test.
    """
    from agent.llm_probe import list_models
    provider = str(body.get("provider") or "").strip().lower()
    if provider in _NATIVE_OAUTH_PROVIDERS:
        from voice.llm.oauth_discovery import list_provider_models
        models, error = await asyncio.to_thread(list_provider_models, provider)
        if models:
            return {"ok": True, "models": models}
        return {"ok": False, "models": [], "error": error or "not signed in"}
    url = str(body.get("url") or "")
    # Off-loop: the safety check resolves DNS (blocking getaddrinfo).
    if url and not await asyncio.to_thread(_llm_probe_url_is_safe, url):
        return {"ok": False, "models": [], "error": "URL not allowed (blocked target)"}
    api_key = str(body.get("api_key") or "")
    # Same "leave blank to keep" fallback as /api/llm/test (see there).
    if not api_key:
        try:
            saved = _resolve_skill_llm(get_active_persona())
            if not url or url == saved.get("url"):
                api_key = saved.get("api_key") or ""
        except Exception:
            pass
    return await list_models(url=url, api_key=api_key)


def _ollama_url_is_safe(url: str) -> bool:
    """Reject non-local Ollama URLs to prevent the unauth ``/api/llm/pull``
    route from being weaponized as an SSRF gadget.

    The route is unauth (the wizard runs before an API key is set up).
    Without this guard, a malicious page in the WKWebView (or anyone
    on 127.0.0.1 with the ephemeral port) could pass any URL and have
    the sidecar POST to it — most concerningly the cloud-metadata
    endpoint at 169.254.169.254 if the user later runs ORBIS on a
    cloud host.

    Allowed:
      - http(s) scheme
      - loopback by name (``localhost``, ``ip6-localhost``)
      - loopback or RFC-1918 private IPs (127.0.0.0/8, 10/8, 172.16/12,
        192.168/16, fc00::/7, ::1)
      - mDNS/Tailscale-style hostnames (``*.local``, ``*.lan``,
        ``*.ts.net``) so users with Ollama on another box on their
        tailnet still work

    Rejected: everything else, including link-local 169.254.x.x
    (cloud metadata) and any public hostname/IP.
    """
    import ipaddress
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "ip6-localhost", "ip6-loopback"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostname (not an IP literal). Constrain to local-network suffixes
        # that aren't routable on the public internet.
        return host.endswith(".local") or host.endswith(".lan") or host.endswith(".ts.net")
    # IP literal — accept loopback + private; reject link-local, the
    # all-zeros unspecified address (which on Linux means "any
    # interface" and would be a confused-deputy invitation), and
    # multicast. Python's `is_private` includes 169.254.0.0/16 (and
    # IPv6 fe80::/10), which is exactly the cloud-metadata range we
    # need to keep blocked, so check those out explicitly.
    if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return False
    return ip.is_loopback or ip.is_private


@router.post("/api/llm/pull")
async def llm_pull(body: dict):
    """Stream an Ollama pull as Server-Sent Events.

    The wizard calls this when a user picks Ollama and the
    recommended model isn't installed yet — instead of asking them
    to drop into a terminal and run ``ollama pull <name>``, we
    proxy Ollama's native ``/api/pull`` and forward each NDJSON
    progress chunk as an SSE message. The frontend renders a
    progress bar from the ``completed`` / ``total`` fields.

    Body::

        {"name": "gemma3n:e2b", "url": "http://127.0.0.1:11434"}

    The ``url`` defaults to the local Ollama instance; we trim any
    trailing ``/v1`` so the same value used as ``llm.url`` for the
    OpenAI-compat endpoint also works here.

    Unauth — same rationale as ``/api/llm/detect_local``: this runs
    before the wizard has set up an API key. URL is constrained by
    ``_ollama_url_is_safe`` so the route can't be turned into an
    SSRF gadget.
    """
    from fastapi.responses import StreamingResponse
    import httpx as _httpx

    name = str(body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "missing model name"}, status_code=400)

    raw_url = str(body.get("url") or "http://127.0.0.1:11434").rstrip("/")
    if raw_url.endswith("/v1"):
        raw_url = raw_url[:-3]
    if not _ollama_url_is_safe(raw_url):
        # Reject before opening a connection. The error is intentionally
        # specific — the wizard prompts on the response, and there's
        # no information leak: the validator only inspects the URL the
        # caller already supplied.
        return JSONResponse(
            {"error": f"refusing to proxy non-local Ollama URL: {raw_url}"},
            status_code=400,
        )

    async def _stream():
        timeout = _httpx.Timeout(connect=5.0, read=None, write=None, pool=None)
        async with _httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{raw_url}/api/pull",
                    json={"model": name, "stream": True},
                ) as resp:
                    if resp.status_code != 200:
                        msg = await resp.aread()
                        yield f"event: error\ndata: {msg.decode(errors='replace')[:200]}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        # Ollama emits NDJSON; pass each line through
                        # as the data of an SSE message. Frontend just
                        # JSON.parses each event.data.
                        yield f"data: {line}\n\n"
                    yield "event: done\ndata: {}\n\n"
            except _httpx.HTTPError as e:
                yield f"event: error\ndata: {str(e)[:200]}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/llm/mlx/pull")
async def llm_mlx_pull(body: dict):
    """Stream an MLX model download (via huggingface_hub) as SSE.

    Frontend equivalent of ``/api/llm/pull`` but for the MLX path —
    when the wizard's user picks the Built-in (MLX) preset, this
    endpoint downloads the chosen ``mlx-community/...`` repo into
    the HF cache directly so the first voice session doesn't pay
    the multi-GB download cost. Emits ``data: {status, completed,
    total}`` progress events while the download runs, then a final
    ``event: done``.

    Body: ``{"model": "mlx-community/gemma-3n-E2B-it-4bit"}``

    Unauth — same rationale as ``/api/llm/detect_local``.
    """
    from fastapi.responses import StreamingResponse
    import asyncio as _asyncio
    from pathlib import Path as _Path

    model_id = str(body.get("model") or "").strip()
    if not model_id or "/" not in model_id:
        return JSONResponse(
            {"error": "model id required (e.g. mlx-community/gemma-3n-E2B-it-4bit)"},
            status_code=400,
        )

    async def _stream():
        try:
            from huggingface_hub import snapshot_download, HfApi
        except ImportError as e:
            yield f"event: error\ndata: huggingface_hub not available: {e}\n\n"
            return

        loop = _asyncio.get_running_loop()
        # HF_HOME can be set to override; otherwise the default is the
        # XDG-ish cache. Read it from the env so we look in the same
        # place huggingface_hub will write to.
        hf_home = os.environ.get(
            "HF_HOME", str(_Path.home() / ".cache/huggingface")
        )
        cache_dir = _Path(hf_home) / "hub" / f"models--{model_id.replace('/', '--')}"

        def _dir_size(p: _Path) -> int:
            if not p.exists():
                return 0
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

        # Yield an immediate "starting" so the frontend stops sitting
        # at zero while the size-probe runs.
        yield 'data: {"status": "fetching repo metadata", "completed": 0, "total": 0}\n\n'

        # Get total size in an executor — HfApi is sync.
        total_bytes = 0
        try:
            info = await loop.run_in_executor(
                None, lambda: HfApi().repo_info(model_id, files_metadata=True)
            )
            for f in info.siblings or []:
                if f.size:
                    total_bytes += f.size
        except Exception as e:
            yield (
                f'data: {{"status": "couldn\\u0027t read total size, '
                f'progress percent will be missing: {str(e)[:100]}", '
                f'"completed": 0, "total": 0}}\n\n'
            )

        yield (
            f'data: {{"status": "downloading", "completed": 0, '
            f'"total": {total_bytes}}}\n\n'
        )

        fut = loop.run_in_executor(None, snapshot_download, model_id)

        last = -1
        while not fut.done():
            completed = _dir_size(cache_dir)
            if completed != last:
                yield (
                    f'data: {{"status": "downloading", '
                    f'"completed": {completed}, "total": {total_bytes}}}\n\n'
                )
                last = completed
            await _asyncio.sleep(0.4)

        try:
            await fut
        except Exception as e:
            yield f"event: error\ndata: {str(e)[:200]}\n\n"
            return

        completed = _dir_size(cache_dir) or total_bytes
        yield (
            f'data: {{"status": "done", '
            f'"completed": {completed}, "total": {total_bytes or completed}}}\n\n'
        )
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/llm/detect_local")
async def llm_detect_local():
    """Parallel probe Ollama (:11434) + LM Studio (:1234) on localhost.
    Returns only the providers that responded — voice-first homelab
    users get a "we noticed your local Ollama" callout in the wizard.

    Unauth — localhost detection before auth is set is the whole point.
    """
    from agent.llm_probe import detect_local
    return await detect_local()


# ── OAuth subscription providers (Claude / ChatGPT-Codex sign-in) ─────────────
#
# All unauth for the same reason as /api/llm/test: the setup wizard runs before
# the owner API key exists, and what's being established here is the user's
# *provider* credential. The flows only ever write ORBIS's own credential store
# (never the vendor CLI's auth files) and the server binds loopback.


@router.get("/api/llm/oauth/status")
async def llm_oauth_status():
    """Sign-in status for every OAuth subscription provider. Read-only."""
    from voice.llm.oauth_discovery import all_oauth_status
    return {"ok": True, "providers": await asyncio.to_thread(all_oauth_status)}


@router.post("/api/llm/oauth/start")
async def llm_oauth_start(body: dict):
    """Begin a sign-in. Body: ``{provider}``. Codex returns a device
    ``user_code`` + ``verification_uri`` to poll on; Claude returns an
    ``authorize_url`` whose displayed code is pasted back to /complete."""
    from voice.llm.oauth_login import OAuthLoginError, login_start
    try:
        flow = await asyncio.to_thread(login_start, str(body.get("provider") or ""))
        return {"ok": True, **flow}
    except OAuthLoginError as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/llm/oauth/poll")
async def llm_oauth_poll(body: dict):
    """One device-flow poll tick (openai-codex). Body: ``{flow_id}``.
    Returns ``{status: pending|complete|error}``; on complete the tokens
    are already stored."""
    from voice.llm.oauth_login import OAuthLoginError, codex_login_poll
    try:
        return await asyncio.to_thread(codex_login_poll, str(body.get("flow_id") or ""))
    except OAuthLoginError as e:
        return {"status": "error", "error": str(e)}


@router.post("/api/llm/oauth/complete")
async def llm_oauth_complete(body: dict):
    """Finish the Claude PKCE flow with the pasted ``code#state``.
    Body: ``{flow_id, code}``."""
    from voice.llm.oauth_login import OAuthLoginError, anthropic_login_complete
    try:
        return await asyncio.to_thread(
            anthropic_login_complete,
            str(body.get("flow_id") or ""),
            str(body.get("code") or ""),
        )
    except OAuthLoginError as e:
        return {"status": "error", "error": str(e)}


@router.post("/api/llm/oauth/cancel")
async def llm_oauth_cancel(body: dict):
    """Abandon an in-progress sign-in. Body: ``{flow_id}``. Idempotent."""
    from voice.llm.oauth_login import cancel_login
    return cancel_login(str(body.get("flow_id") or ""))


@router.post("/api/llm/oauth/disconnect")
async def llm_oauth_disconnect(body: dict):
    """Disconnect a provider: best-effort revoke of ORBIS-minted tokens, delete
    ORBIS's credential copy, and suppress auto-resolve until the next sign-in.
    Never touches the vendor CLI's own auth files. Body: ``{provider}``."""
    from voice.llm.oauth import OAuthCredentialError, disconnect
    try:
        res = await asyncio.to_thread(disconnect, str(body.get("provider") or ""))
        return {"ok": True, **res.as_dict()}
    except OAuthCredentialError as e:
        return {"ok": False, "error": str(e)}
