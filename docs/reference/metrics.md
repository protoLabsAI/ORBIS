# Metrics

`GET /api/metrics` exposes in-process counters. Reset on each server restart.

## Example response

```json
{
  "boot_at": 1776665432.1,
  "sessions_total": 7,
  "sessions_active": 1,
  "a2a_inbound_total": 12,
  "tool_calls_total": 23,
  "tool_calls_by_name": {
    "web_search": 11,
    "deep_research": 5,
    "get_datetime": 3,
    "calculator": 2,
    "a2a_dispatch": 2
  },
  "clone_requests_total": 1,
  "uptime_secs": 3622.4
}
```

## Fields

| Field | Type | Meaning |
|:---|:---|:---|
| `boot_at` | unix ts | When the server started |
| `uptime_secs` | float | Seconds since `boot_at` |
| `sessions_total` | int | Lifetime WebRTC connections handled |
| `sessions_active` | int | Currently connected clients (0 or 1 for now) |
| `a2a_inbound_total` | int | Inbound `message/send` requests |
| `tool_calls_total` | int | Tool dispatches — sync + async |
| `tool_calls_by_name` | dict | Per-tool dispatch counts |
| `clone_requests_total` | int | Successful `/api/voice/clone` uploads |

## What's NOT here

- **Latency histograms.** Run `scripts/bench.py` for those — see [Benchmarking](/guides/benchmarking).
- **Per-session traces.** Pipecat DEBUG logs include everything; tail the log for forensic replay.
- **Prometheus format.** Intentional — protoVoice is single-node; if you want Prom, scrape `/api/metrics` as JSON and transform on the collector side.

## Usage

```bash
watch -n 5 'curl -s http://localhost:7867/api/metrics | jq .'
```

Reset is "restart the server" — there's no endpoint to zero the counters. This is deliberate; the counters are cheap to reset and process restarts are already a common occurrence during active development.
