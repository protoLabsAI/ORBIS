# Widgets

A **widget** is an openable ambient readout — a small floating panel the user
opens (by voice or the launcher) and glances at, like Weather. Widgets are
**voice-first**: minimal chrome, no input forms; voice sets their state. Keep
them JARVIS-ambient, not app-like.

A widget is defined in **two places that share an `id`** — and a test
(`tests/test_widgets.py`) fails CI if they drift:

1. **Render** (this folder) — the React component, auto-discovered by the eager
   glob in `widgets/index.ts`.
2. **Voice catalog** (`config/widgets.yaml`) — what the sidecar tells the model
   it can open, and which props it accepts.

## Add one

**1. Render** — `web/src/widgets/<id>/index.tsx`:

```tsx
import { Clock } from 'lucide-react';
import { registerWidget } from '@/sdk';
import { Timer } from './Timer';

registerWidget({
  id: 'timer',                 // must match the catalog id below
  title: 'Timer',
  icon: Clock,
  klass: 'glance',             // 'glance' | 'content' | 'agent'
  render: Timer,
});
```

Your component receives `WidgetProps` (`{ id, surface, props }`). It never knows
*where* it lives — `surface` is `'dock'` (a floating card) or `'window'` (a
popped-out native window); render the same either way. Voice-set state arrives in
`props` (see below). Fetch your own data in the component — `weather/Weather.tsx`
is the reference (effect keyed on `props.location`).

**2. Voice catalog** — add to `config/widgets.yaml` with the **same id**:

```yaml
  - id: timer
    title: Timer
    description: a countdown the user can glance at
    props:
      - name: minutes
        description: how long to count down
```

That's it — no Python code. The sidecar reads the catalog to gate the
`render_widget` tool and teach the model each widget's props.

## How voice drives it

When the user says "set a 5-minute timer", the model calls
`render_widget(widget="timer", action="open", props={ minutes: "5" })`. The
sidecar forwards `props` to the frontend over the SSE bus; your component
re-renders with the new `props`. `action: "close"` hides it.

## Surfaces

Pure-frontend widgets work with zero Rust changes — they live in the dock. The
pop-out-to-native-window path (`open_widget_window`) is generic; you don't write
Tauri code for a normal widget.

See [CONTRIBUTING.md](../../../CONTRIBUTING.md) and the widget philosophy in
[docs/internal/surface-plan.md](../../../docs/internal/surface-plan.md).
