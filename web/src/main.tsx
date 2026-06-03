import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { getCurrentWindow } from '@tauri-apps/api/window'
import './index.css'
import App from './App.tsx'
import { WidgetWindowRoot } from './widgets/WidgetWindowRoot'
import { CommandBar } from './command/CommandBar'
import { MiniOrb } from './widgets/MiniOrb'
import './widgets' // register widgets so a popped-out window can render them

// Secondary windows ride a transparent native window; index.css paints html/body
// opaque (#0a0a0a), so clear those so the window's transparency shows through.
function makeTransparent() {
  document.documentElement.style.background = 'transparent'
  document.body.style.background = 'transparent'
}

/**
 * Same bundle, many roots, routed by window label: the main window renders
 * <App/> (orb + chrome); a popped-out widget renders just that widget; the
 * command bar and the ambient mini-orb get their own roots.
 */
function rootFor() {
  try {
    const label = getCurrentWindow().label
    if (label.startsWith('widget-')) {
      makeTransparent()
      return <WidgetWindowRoot id={label.slice('widget-'.length)} />
    }
    if (label === 'command-bar') {
      makeTransparent()
      return <CommandBar />
    }
    if (label === 'mini-orb') {
      makeTransparent()
      return <MiniOrb />
    }
  } catch {
    // not running inside a Tauri window — fall through to the app
  }
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>{rootFor()}</StrictMode>,
)
