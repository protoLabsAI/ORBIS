# Use ORBIS from the Dock or menu bar

ORBIS stays running as a background companion when you close its window. This
guide covers how to find, show, and fully quit it.

## Find it

The installed ORBIS app stays in the **Dock** and app switcher as its dependable
way back to the app. It also creates an **ORBIS orb** in the macOS menu bar
(top-right of the screen) when macOS presents the native status item.

The Dock and app-switcher presence is intentional. In a macOS 26.5.2
reproduction, the Tauri status item appeared briefly during launch and was gone
after the app loaded while the shell kept running. The Accessory-policy
transition was the implicated boundary, so ORBIS now retains the independent
Regular/Dock path while the underlying tray behavior is investigated.

If an installed and a development copy are both running, quit the one you do
not intend to use before diagnosing the menu bar. A visible development tray
has the tooltip **ORBIS (development)**; the logs also record each process's
PID and executable path. A crowded or notched menu bar can hide status items
even when both processes are healthy.

The dependable Dock behavior above describes the installed `.app`; `tauri dev`
is a different macOS launch mode and should be treated as diagnostic only.

## Show the window

Click the **Dock icon**, or left-click the menu-bar orb when it is visible, to
bring the ORBIS window forward.

## Hide it (keep it running)

Close the window — the **red traffic-light** button, or **⌘W**. The window
disappears but **ORBIS keeps running and listening**. You can still talk to it
and it'll respond; only the orb visual is hidden. Click the Dock icon or the
menu-bar orb to bring it back.

## Quit it (stop it completely)

Use the menu-bar orb → **Quit ORBIS**, **⌘Q**, or the Dock menu's **Quit**. This
fully exits and shuts down the voice pipeline. Closing the window alone does
not stop ORBIS.

## Why it works this way

A voice companion should be *available*, not a window you keep re-opening.
Closing-to-hide keeps it running in the background; the retained Dock icon
ensures there is still a visible way back when the menu-bar item is not visible.
For the full behaviour, see [The desktop app](/reference/desktop).
