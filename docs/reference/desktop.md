# The desktop app

ORBIS is a native macOS app that runs as a **menu-bar agent** — always available,
out of your way. This page covers how the app behaves on your Mac.

## Menu-bar agent

ORBIS lives in the **menu bar**, not the dock. The protoLabs robot icon up top
is your handle to it:

- **Left-click** the icon → show the orb window.
- **The icon's menu** → **Show ORBIS** / **Quit ORBIS**.

There's intentionally **no dock icon** — ORBIS is a background companion, like a
menu-bar utility.

## Closing vs. quitting

- **Close the window** (the red traffic-light, or ⌘W) → the window **hides**.
  ORBIS keeps running: the voice loop, your sidecar, and audio stay alive, so it
  keeps listening in the background. Bring it back from the menu-bar icon.
- **Quit** (menu-bar icon → **Quit ORBIS**) → fully exits and shuts the sidecar
  down cleanly. This is the real "stop ORBIS."

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
is **Quit** from the menu bar and relaunch.

## Installing

Download the signed, notarized `.dmg` from
[orbis.protolabs.studio/download](https://orbis.protolabs.studio/download) and
drag ORBIS into Applications — see [Getting started](/tutorials/getting-started).

## See also

- [Run ORBIS as a menu-bar agent](/how-to/menu-bar-mode)
- [Access & privacy](./access)
