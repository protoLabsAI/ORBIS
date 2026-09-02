# The desktop app

ORBIS is a native macOS app that keeps running as a background companion when
its window is closed. This page covers how the app behaves on your Mac.

## Dock and menu-bar access

The installed ORBIS app retains its **Dock icon** and app-switcher presence as
the dependable handle to a running app. It also creates a native ORBIS status
item for the menu bar:

- **Click the Dock icon** → show the orb window.
- **Left-click** the icon → show the orb window.
- **The icon's menu** → **Show ORBIS** / **Quit ORBIS**.

The Dock and app-switcher presence is deliberate. ORBIS previously scheduled
macOS's Accessory activation policy after creating the tray. In a macOS 26.5.2
reproduction, the status item appeared briefly during launch and was gone once
the app loaded while the shell remained alive. Accessory is the implicated
policy boundary, not yet a proven root cause. Staying in Regular/Dock mode
removes that variable and preserves an independent path back to the running app
even if the status item is absent or later hidden by macOS.

### Installed and development builds

An installed ORBIS and `tauri dev` are separate shell and tray processes. Quit
the copy you do not intend to use before diagnosing a missing icon because the
extra item can crowd the menu bar and make the observation ambiguous. When a
development tray is visible, its hover label says **ORBIS (development)**; a
release says **ORBIS**.

The Dock invariant applies to the installed `.app`. `tauri dev` uses a
different macOS launch mode, so its Dock and tray behavior is diagnostic rather
than a supported product guarantee.

Startup logs record a tray only after its native builder succeeds and Tauri can
look up the process-local registration. The record includes the PID,
development/release kind, bundle identifier, and executable path so simultaneous
copies can be distinguished. ORBIS checks that registration again on a macOS
Dock reopen and recreates the tray if the registration was removed. A registry
lookup does not prove the icon is visually present: Tauri exposes no event for
an item that macOS hides or fails to present. The Dock remains available in
either case.

## Closing vs. quitting

- **Close the window** (the red traffic-light, or ⌘W) → the window **hides**.
  ORBIS keeps running: the voice loop, your sidecar, and audio stay alive, so it
  keeps listening in the background. Bring it back from the Dock or menu-bar
  icon.
- **Quit** (⌘Q, Dock menu → **Quit**, or menu-bar orb → **Quit ORBIS**) → fully
  exits and shuts the sidecar down cleanly. This is the real "stop ORBIS."

## The window

When it's visible, the orb fills the window edge-to-edge (an immersive title
bar — the orb runs to the top). The chrome is minimal:

- **Top-right gear** → Settings.
- **Reminders bell** (below the gear) → your scheduled reminders; a dot, tinted
  to the orb's colour, appears when any are set.
- **Double-click the orb** → start a voice turn.

## Under the hood

The app is a thin native shell plus a local **sidecar** (the brain + voice
pipeline) that it launches and supervises. Everything runs on `127.0.0.1`;
nothing is exposed to the network. If something feels stuck, the cleanest reset
is to fully **Quit** from the Dock or menu-bar orb and relaunch.

## Installing

Download the signed, notarized `.dmg` from
[orbis.protolabs.studio/download](https://orbis.protolabs.studio/download) and
drag ORBIS into Applications — see [Getting started](/tutorials/getting-started).

## See also

- [Use ORBIS from the Dock or menu bar](/how-to/menu-bar-mode)
- [Access & privacy](./access)
