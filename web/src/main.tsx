import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { getCurrentWindow } from '@tauri-apps/api/window'
import './index.css'
import App from './App.tsx'
import { WidgetWindowRoot } from './widgets/WidgetWindowRoot'
import { CommandBar } from './command/CommandBar'
import './widgets' // register widgets so a popped-out window can render them

/**
 * Same bundle, two roots: the main window renders <App/> (orb + chrome); a
 * popped-out widget window (label "widget-<id>") renders just that widget
 * full-bleed. The window label is the routing signal — no query string needed.
 */
function rootFor() {
  try {
    const label = getCurrentWindow().label
    // Both secondary windows ride a transparent native window; index.css paints
    // html/body opaque (#0a0a0a), so clear those so the glass shows through.
    if (label.startsWith('widget-')) {
      document.documentElement.style.background = 'transparent'
      document.body.style.background = 'transparent'
      return <WidgetWindowRoot id={label.slice('widget-'.length)} />
    }
    if (label === 'command-bar') {
      document.documentElement.style.background = 'transparent'
      document.body.style.background = 'transparent'
      return <CommandBar />
    }
  } catch {
    // not running inside a Tauri window — fall through to the app
  }
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>{rootFor()}</StrictMode>,
)
