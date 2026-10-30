# First L2 canary run — 2026-10-29 (qa.fleetdm.com 4.94.0-rc.2610272345, fleet 65)

macOS host 631 (UTM, Apple Silicon): 10/13 pass. Windows host 629 (Parallels, Windows 11 on ARM): 4/10 pass.

| App | Host | Result | Verdict |
|---|---|---|---|
| fleet-desktop, google-chrome, firefox, claude, docker-desktop, onedrive, postman, figma, santa, zoom | 631 | pass | — |
| signal/darwin | 631 | "still on host after uninstall" | Harness race (polled before the uninstall was pending); app was removed. Fixed. |
| teleport-suite/darwin | 631 | install still pending at 30 min | Slow downloads from qa.fleetdm.com; installed late, removed by hand. |
| microsoft-defender/darwin | 631 | add: 500 "mismatch in maintained app SHA256 hash" | #55360 — the issue's original app, reproduced |
| firefox, firefox@developer-edition, firefox@nightly, zoom | 629 | pass | — |
| claude/windows | 629 | inventory 2.42304.2.0 vs package 2.42304.2 | Harness too strict (trailing .0 is equal). Fixed. |
| figma/windows | 629 | round 1: installed but not present yet | Per-user installer registers after exit; round 2 fine. Grace period added. |
| signal/windows | 629 | 3 of 5 installs: "Software installer download failed" | qa.fleetdm.com serves installers at ~67 KB/s (measured); Fleet auto-retried. Environment. |
| docker/windows | 629 | install failed after 20 min | Windows on ARM (nested virtualization) + slow download. Environment. |
| postman/windows | 629 | add: 500 "mismatch in maintained app SHA256 hash" | #55360 |
| google-chrome/windows | 629 | "installed" but Chrome never on disk; uninstall always 1603/1721 | See below |

## google-chrome/windows on Windows on ARM
The x64 enterprise MSI installs Google Updater and an MSI registration but never puts Chrome on disk (first uninstall at 03:24 already logged Error 1721 with an empty uninstaller location). MSI exits 0, so Fleet shows "installed" and inventory/exists report Chrome 156.0.8078.74. Uninstall can never succeed. Orphaned registration removed twice (cleanup-chrome-orphan.ps1). Open question: ARM-specific or SYSTEM-context? Next: a person installs the same MSI by hand on 629.

## Environment notes
- qa.fleetdm.com serves installer bytes itself at ~67 KB/s (39 MB Signal package: 571 s from a Mac), so large installers time out on hosts and cycle times balloon (Windows installs 13–20 min).
- Live queries return "timeout waiting for results" while a host is busy installing; the runner retries.
