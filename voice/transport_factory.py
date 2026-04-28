"""Transport factory — returns the right Pipecat transport based on AUDIO_TRANSPORT env var.

Usage:
    from voice.transport_factory import make_transport, AUDIO_TRANSPORT

    transport = make_transport(webrtc_connection=conn)   # webrtc mode
    transport = make_transport()                          # native mode (no conn needed)
"""

import os

from pipecat.transports.base_transport import TransportParams

AUDIO_TRANSPORT: str = os.environ.get("AUDIO_TRANSPORT", "webrtc").lower()

# In native mode the Rust engine already does real AEC, so EchoGuard
# should be a no-op. This override is read by app.py when building the
# EchoGuardSuppressor and EchoGuardObserver.
NATIVE_ECHO_GUARD_MS: int = 0


def make_transport(webrtc_connection=None, audio_in_filter=None):
    """Return the appropriate transport for the current AUDIO_TRANSPORT setting.

    Args:
        webrtc_connection: Required when AUDIO_TRANSPORT=webrtc. Ignored in native mode.
        audio_in_filter:   Optional pipecat audio in-filter (e.g. RNNoise).
                           Applied in webrtc mode only; native mode AEC runs in Rust.

    Returns:
        SmallWebRTCTransport (webrtc) or LocalAudioTransport (native).
    """
    if AUDIO_TRANSPORT == "native":
        sock_path = os.environ.get("ORBIS_AUDIO_SOCK", "")
        if not sock_path:
            raise RuntimeError(
                "AUDIO_TRANSPORT=native but ORBIS_AUDIO_SOCK is not set. "
                "Ensure the Rust sidecar started with native-audio feature enabled."
            )
        from voice.local_transport import LocalAudioTransport
        return LocalAudioTransport(sock_path=sock_path)

    # Default: webrtc
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    if webrtc_connection is None:
        raise ValueError(
            "make_transport() requires webrtc_connection when AUDIO_TRANSPORT=webrtc"
        )
    return SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
            audio_in_filter=audio_in_filter,
        ),
    )
