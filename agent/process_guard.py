"""Process-tree lifecycle guard for the sidecar (#485).

The sidecar is a process *tree* — uvicorn + Pipecat + MLX/Whisper/Kokoro
workers. The Tauri shell used to reap it with a single SIGKILL to the direct
child, which does NOT collect grandchildren: they orphaned and kept holding
port 7866 / the GPU, so repeated launch→quit cycles leaked workers until the
next launch hit a port collision or OOM. And on a Rust panic the shell's exit
handler never fires at all → guaranteed orphan.

Two mechanisms close that:

1. ``establish_own_process_group()`` — the sidecar ``setsid()``s into its own
   session/group at startup, so its whole tree shares one process-group id the
   shell can signal with ``kill -TERM/-KILL -<pgid>`` (see the Rust side). We
   only advertise the pgid when setsid actually succeeded, so the shell reaps
   the group we *own* — never the app's own group by accident.

2. ``start_parent_death_watchdog()`` — a daemon thread that notices when the
   parent PID changes (the shell died / was killed / panicked, and we got
   reparented to launchd) and reaps our own group. This is the only thing that
   saves the panic case, where no clean shutdown ever runs.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

logger = logging.getLogger(__name__)


def establish_own_process_group() -> int | None:
    """Make this process a session/group leader via ``setsid()`` so the whole
    subprocess tree shares one process-group id. Returns that pgid on success,
    or ``None`` if setsid failed (e.g. we're already a group leader) — in which
    case the caller must NOT advertise a pgid, so the shell falls back to the
    old direct-child kill rather than signalling a group we don't own.
    """
    if not hasattr(os, "setsid"):  # non-POSIX; nothing to do
        return None
    try:
        os.setsid()
    except OSError as e:
        logger.warning(f"[process-guard] setsid failed ({e}); no group reaping")
        return None
    pgid = os.getpgrp()
    logger.info(f"[process-guard] own process group established pgid={pgid}")
    return pgid


def _reap_group(pgid: int) -> None:
    """SIGKILL our own process group — reaps every same-group descendant
    (uvicorn, MLX, Pipecat workers) still holding the port / GPU."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError as e:  # group already gone
        logger.debug(f"[process-guard] killpg({pgid}) noop: {e}")


def _watchdog_loop(
    original_ppid: int,
    pgid: int,
    *,
    poll_secs: float,
    getppid=os.getppid,
    reap=_reap_group,
    sleep=time.sleep,
    max_iters: int | None = None,
) -> None:
    """Poll for reparenting. When our parent PID changes from the one we
    started under, the shell process is gone (crash / panic / SIGKILL) and we
    were reparented (to launchd/init) — reap the group so we don't linger
    holding the port. The injectable ``getppid``/``reap``/``sleep``/``max_iters``
    make this unit-testable without a real orphaning.
    """
    iters = 0
    while max_iters is None or iters < max_iters:
        current_ppid = getppid()
        if current_ppid != original_ppid:
            logger.warning(
                f"[process-guard] parent {original_ppid} gone (now {current_ppid}); "
                f"reaping group {pgid}"
            )
            reap(pgid)
            return
        iters += 1
        sleep(poll_secs)


def start_parent_death_watchdog(pgid: int, *, poll_secs: float = 2.0) -> threading.Thread:
    """Start the parent-death watchdog as a daemon thread. Requires the pgid
    from ``establish_own_process_group()`` so the reap targets our own group."""
    original_ppid = os.getppid()
    t = threading.Thread(
        target=_watchdog_loop,
        args=(original_ppid, pgid),
        kwargs={"poll_secs": poll_secs},
        name="orbis-parent-watchdog",
        daemon=True,
    )
    t.start()
    logger.info(
        f"[process-guard] parent-death watchdog armed (parent={original_ppid}, "
        f"pgid={pgid})"
    )
    return t
