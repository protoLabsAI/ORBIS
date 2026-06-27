/**
 * The invoke() router — every native command the app calls, answered for
 * the browser. HTTP (api_request) delegates to the local backend router;
 * device/window commands return safe defaults or no-op (PR1 has no real
 * audio engine — that arrives with the on-device voice loop in PR3).
 */
import { httpRequest, type ApiRequestArgs } from '../backend/router';

export async function handleInvoke(
  cmd: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  switch (cmd) {
    // --- backend ---
    case 'api_request':
      return httpRequest(args as unknown as ApiRequestArgs);
    case 'backend_url':
      // backendBase() has already resolved to the page origin (https), so
      // returning null here is fine — the proxy URL is never actually used.
      return null;
    case 'boot_status':
      // BootStatus opens the gate the moment it sees a 'ready' marker.
      return JSON.stringify({ stage: 'ready', detail: 'Ready' });

    // --- audio / mic getters: no device backend in the browser (PR1) ---
    case 'get_audio_level':
      return 0;
    case 'get_audio_levels':
      // Shape MUST match the Rust command: { mic, playback }. The orb's
      // audio-envelope hook reads nat.playback / nat.mic — wrong keys make
      // `undefined * gain = NaN`, which poisons uDensity → a black orb.
      return { mic: 0, playback: 0 };
    case 'list_audio_inputs':
    case 'list_audio_outputs':
      return [];
    case 'get_audio_input_mode':
      return 'half_duplex';
    case 'get_microphone_permission_status':
      return 'granted';
    case 'mic_listening':
      return false;

    // --- config getters ---
    case 'get_activation_config':
      return {
        enabled: false,
        model: null,
        threshold: 0.5,
        listen_window_secs: 30,
        full_duplex: false,
      };
    case 'get_discoverable':
      return false;
    case 'surface_enabled':
      return false;

    // --- side effects we can honor in the browser ---
    case 'open_url': {
      const url = args.url as string | undefined;
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
      return null;
    }

    // --- setters / window ops / one-shots: no-op in the demo ---
    case 'set_mic_listening':
    case 'set_mic_muted':
    case 'set_input_device':
    case 'set_output_device':
    case 'start_audio_engine':
    case 'set_activation_config':
    case 'set_full_duplex':
    case 'set_discoverable':
    case 'request_microphone_permission':
    case 'open_microphone_settings':
    case 'reveal_logs':
    case 'clear_browsing_data':
    case 'show_main':
    case 'open_widget_window':
    case 'close_widget_window':
      return null;

    default:
      console.warn(`[demo] unhandled invoke('${cmd}')`);
      return null;
  }
}
