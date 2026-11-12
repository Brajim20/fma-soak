## FMA soak — November 2026 update

**Environment:** qa.fleetdm.com, fleet 65 · builds: 4.94.0-rc.2610272345 (937e87458c)

**1. Runs** — 9151 cycles: pass 3478, findings 2099, flaky 433, environment 132, harness 282, skipped 2727

**2. Coverage** — darwin 975/963 (101.2%), round 5; windows 462/485 (95.3%), round 5

**3. Escapes caught or confirmed**

| Issue | State | Title |
|---|---|---|
| #42082 | open | Windows FMAs: Support multiple architectures |
| #45895 | merged | DMG install script use yes and update scripts |
| #47037 | merged | Add several Windows FMAs |
| #49123 | closed | Send browser User-Agent when downloading FMA installers |
| #49682 | closed | Show a clear error when adding Mozilla Firefox and Firefox ESR to the same fleet |
| #50077 | open | Windows software with version in name still matches wrong title for apps needing a custom match pattern |
| #50937 | open | Allow multi-file software installers via archive extraction |
| #51793 | open | Build dependency checking and installation into Windows FMAs |
| #51875 | closed | Per-user Windows Fleet-maintained apps install into SYSTEM's profile, where they are unusable and often unremovable |
| #51940 | merged | Fix per-user install context for 32 Windows Fleet-maintained apps |
| #53050 | open | Allow both x64 and ARM64 Windows FMA variants of the same app in one fleet |
| #53828 | merged | Keep Go-http-client token in FMA installer User-Agent |
| #55360 | open | Adding a Fleet-maintained app returns 500 "mismatch in maintained app SHA256 hash" with an unhelpful message |
| #55961 | open | Explore Fleet-maintained apps and build sustained FMA automation |
| #56532 | closed | Sourcetree Fleet-maintained app installs with root-only permissions, so users can't open it |
| #56589 | merged | Unfreeze MEGAsync (darwin) |
| #56802 | merged | Add ARM64 Windows Fleet-maintained apps for 23 popular apps |
| #56873 | open | Fleet-maintained apps: pinning a hashless app to an older version installs the latest version |
| #56891 | open | Fleet-maintained apps: a failed installer download returns 500 with the raw network error |
| #56948 | open | Windows uninstall reports success when the app wasn't installed by its MSI, and the app stays installed |
| #56971 | closed | Fleet-maintained apps: AWS CLI (Windows) is never detected, so its patch policy always passes |
| #57008 | closed | Fleet-maintained apps: MEGAsync (macOS) patch policy fails on up-to-date hosts |
| #57025 | open | macOS `ChatGPT.app` (bundle id `com.openai.codex`) is listed as "Codex" in software inventory — search for "ChatGPT" misses it; three different "Codex" titles on the Software page |
| #57051 | open | Druva InSync (GovCloud) colliding with Druva InSync |
| #57113 | closed | Fleet-maintained apps: REAPER (Windows) is never detected, so its patch policy always passes |
| #57114 | closed | Fleet-maintained apps: Arduino IDE (Windows) install always fails with 1603 (per-user MSI under SYSTEM hits the 260-character path limit) |
| #57116 | closed | Fleet-maintained apps: Evernote (macOS) installs a 2024 build (10.105.4) labeled as 11.40.3 |
| #57118 | closed | Fleet-maintained apps: Sublime Text (Windows) registers no version, so its patch policy fails on every host |
| #57119 | closed | Fleet-maintained apps: Sequel Ace (macOS) detection uses the wrong bundle ID, so installs land on a separate title |
| #57120 | closed | Fleet-maintained apps: JASP (macOS) detection uses an outdated bundle ID (org.jasp-stats.JASP → org.jaspstats.jasp) |
| #57132 | open | Software installers: after a failed upload, uploading the same package again serves a truncated installer (local filesystem store) |
| #57136 | closed | Fleet-maintained apps: Galaxy Modeler (Windows) is never detected, so its patch policy always passes |
| #57137 | closed | Fleet-maintained apps: PostgreSQL 18 (Windows) is never detected (installer registers "PostgreSQL 18 " with a trailing space) |
| #57138 | closed | Fleet-maintained apps: PostgreSQL 18 (Windows) can't be reinstalled after an uninstall, and the uninstall is reported as failed |
| #57139 | open | Fleet-maintained apps: Azul Zulu JRE 25 (Windows) installs land on a separate title (MSI upgrade code skips name match) |
| #57148 | open | fleetd: a timed-out script's child processes keep running, and on macOS an orphaned installer blocks every later package install |
| #57149 | closed | Fleet-maintained apps: 010 Editor (Windows) install fails with exit code 5 while the app is open |
| #57151 | closed | Fleet-maintained apps: OpenCloud Desktop (macOS) detection uses the wrong bundle ID, so installs land on a separate title |
| #57152 | closed | Fleet-maintained apps: ocenaudio and Zettlr (Windows) install into the SYSTEM profile and can't be uninstalled |
| #57153 | closed | Fleet-maintained apps: cmux (macOS) patch policy fails right after installing the current version (0.65.2.2 vs 0.65.2) |
| #57157 | closed | Fleet-maintained apps: Snagit (Windows) is never detected, so its patch policy always passes |
| #57190 | merged | Regenerate macOS zip FMA outputs for ditto extraction (2/4) |
| #57224 | closed | Fleet-maintained apps: Evernote (Windows) is never detected, so its patch policy always passes |
| #57225 | closed | Fleet-maintained apps: AnyBurn (Windows) patch policy fails right after installing the current version (6.9.0.0 vs 6.9) |
| #57321 | closed | Fleet-maintained apps: Webex (Windows) is never detected, so its patch policy always passes |
| #57333 | closed | Fleet-maintained apps: dupeGuru (Windows) is never detected, so its patch policy always passes |
| #57334 | closed | Fleet-maintained apps: Plantronics Hub (Windows) is never detected, so its patch policy always passes |
| #57335 | closed | Fleet-maintained apps: Ollama (Windows) install always fails with exit code 1 |
| #57336 | open | Fleet-maintained apps: Steam (macOS) can't be added: 422 Validation Failed |
| #57337 | closed | Fleet-maintained apps: Wondershare Filmora (macOS) is never detected (bundle ID com.wondershare.filmoramac vs filmoramacos) |
| #57338 | closed | Fleet-maintained apps: WeChat (macOS) patch policy fails right after installing the current version (4.2.0.16 vs 4.2.0) |
| #57341 | closed | Fleet-maintained apps: BitBox (macOS) is never detected (bundle ID ch.shiftcrypto.BitBoxApp vs ch.shiftcrypto.wallet) |
| #57390 | closed | Fleet-maintained apps: Google Drive (Windows) can't be installed again after Fleet uninstalls it (exit 1610) |
| #57391 | closed | Fleet-maintained apps: MonitorControl (macOS) never detected after install (bundle ID mismatch) |
| #57498 | closed | Fleet-maintained apps: draw.io (Windows) exists/patched queries never match the installed app, so its patch policy always passes |
| #57499 | closed | Fleet-maintained apps: Kiro (Windows) install fails with exit code 1 while the app is open |
| #57500 | closed | Fleet-maintained apps: NoSQL Workbench (Windows) is never detected, so its patch policy always passes |
| #57501 | closed | Fleet-maintained apps: Paint.NET (Windows) install always fails and nothing is installed |
| #57502 | closed | Fleet-maintained apps: ShareFile (Windows) is never detected, so its patch policy always passes |
| #57503 | closed | Fleet-maintained apps: Tower (Windows) installs into the SYSTEM profile instead of a machine-wide location |
| #57504 | closed | Fleet-maintained apps: Wacom Tablet (Windows) is never detected, so its patch policy always passes |
| #57506 | closed | Fleet-maintained apps: XQuartz (macOS) is never detected (bundle ID org.macosforge.xquartz.X11 vs org.xquartz.X11) |
| #57547 | closed | Fleet-maintained apps: Descript (Windows) can't be uninstalled — every uninstall fails "The system cannot find the file specified" |
| #57548 | closed | Fleet-maintained apps: Jabra Direct (Windows) is never detected, so its patch policy always passes |
| #57549 | closed | Fleet-maintained apps: Microsoft Teams (Windows) uninstall removes Teams but reports failure (exit 1603) |
| #57550 | closed | Fleet-maintained apps: OBS Studio (Windows) install fails with exit code 2 while the app is open |
| #57551 | closed | Fleet-maintained apps: Scrivener (Windows) installs into the SYSTEM profile instead of a machine-wide location |
| #57552 | open | Software: uninstall scripts are killed after 5 minutes instead of the documented 1 hour |
| #57553 | closed | Fleet-maintained apps: Geany (Windows) is never detected after install, so its patch policy always passes |
| #57554 | closed | Fleet-maintained apps: GoLand (Windows) install doesn't finish while GoLand is open, and the host's queue stays blocked behind it |
| #57555 | closed | Fleet-maintained apps: SQL Server Management Studio (Windows) is never seen as running, so "patch when closed" can't hold the install back |
| #57556 | closed | Fleet-maintained apps: Twine (Windows) installs into the SYSTEM profile and can't be uninstalled |
| #57557 | closed | Fleet-maintained apps: Visual Studio Professional 2022 (Windows) reinstall over an existing install fails with exit code 1 |
| #57558 | closed | Fleet-maintained apps: Twingate (Windows) is never detected, so its patch policy always passes |
| #57601 | closed | Fleet-maintained apps: OpenVPN Connect (Windows) is never detected, so its patch policy always passes |
| #57659 | closed | Fleet-maintained apps: GoAnywhere OpenPGP Studio (Windows) install fails with exit code 1 while the app is open |
| #57660 | closed | Fleet-maintained apps: Standard Notes (Windows) installs into the SYSTEM profile and can't be uninstalled |
| #57673 | closed | Fleet-maintained apps: DevToys (Windows) is never detected, so its patch policy always passes |
| #57674 | closed | Fleet-maintained apps: gPodder (Windows) is never detected, so its patch policy always passes |
| #57681 | open | FMA: Adobe Acrobat Reader (Windows) shows up in inventory under a separate "DC" title, not the Fleet-maintained title |
| #57700 | closed | Fleet-maintained apps: Nagstamon (Windows) is never detected, so its patch policy always passes |
| #57701 | closed | Fleet-maintained apps: Sonos (Windows) install always fails with exit code 1619 |
| #57748 | closed | Fleet-maintained apps: Snagit (macOS) can't be reinstalled or uninstalled after it's been opened ("did not quit" though it isn't running) |
| #57805 | closed | Fleet-maintained apps: Autopsy (Windows) is never detected after install, so its patch policy always passes |
| #57806 | closed | Fleet-maintained apps: Burp Suite Community Edition (macOS) is never detected after install |
| #57807 | closed | Fleet-maintained apps: Mozilla VPN (macOS) can't be updated or uninstalled after Fleet installs it ("did not quit") |
| #57808 | closed | Fleet-maintained apps: Mozilla VPN (macOS) install hangs until Fleet kills it on some Macs, blocking the host's queue for an hour |
| #57809 | closed | Fleet-maintained apps: RStudio (Windows) install fails with exit code 2 while the app is open |
| #57810 | closed | Fleet-maintained apps: XnView MP (Windows) is never detected after install, so its patch policy always passes |
| #57817 | closed | Fleet-maintained apps: Processing (macOS) is never detected after install (bundle ID org.processing.four vs org.processing.app) |
| #57818 | closed | Fleet-maintained apps: Elgato Wave Link (macOS) is never detected after install (bundle ID com.elgato.WaveLink vs com.elgato.WaveLink3) |
| #57819 | closed | Fleet-maintained apps: OnionShare (macOS) is never detected after install (bundle ID org.onionshare.onionshare vs OnionShare) |
| #57827 | open | Fleet-maintained apps: Crestron AirMedia (Windows) can't be added — vendor returns 403 to Fleet's download User-Agent (regression of #49123) |
| #57888 | merged | Fix GoLand (Windows) reinstall hanging on the installer's non-empty-directory prompt |
| #57915 | closed | Fleet-maintained apps: Novabench (macOS) installs root-only, so users can't open it |
| #57916 | open | Software: adding a macOS app attaches it to another app's title when only the name matches (Recents → Apple's Finder Recents) |
| #57917 | closed | Fleet-maintained apps: Requestly (Windows) installs into the SYSTEM profile and can't be uninstalled |
| #57918 | closed | Fleet-maintained apps: Stretchly (Windows) installs into the SYSTEM profile and can't be uninstalled |
| #57919 | closed | Fleet-maintained apps: Syncovery (macOS) patch policy fails right after installing the current version |
| #57920 | closed | Fleet-maintained apps: Tailscale (macOS) can't be updated or uninstalled after Fleet installs it ("did not quit") |
| #57921 | closed | Fleet-maintained apps: Thorium Reader (Windows) installs into the SYSTEM profile and can't be uninstalled |
| #57922 | closed | Fleet-maintained apps: Zed (Windows) installs into the SYSTEM profile instead of a machine-wide location |
| #57923 | open | Fleet-maintained apps: coconutBattery (macOS) never shows an installed version (catalog bundle ID is its login-item helper) |
| #57924 | closed | Fleet-maintained apps: WezTerm (macOS) can't be updated while it's open ("did not quit") |
| #57957 | closed | Fleet-maintained apps: Affinity (macOS) install over the open app quits it and doesn't relaunch it |
| #58021 | closed | Fleet-maintained apps: Burp Suite Professional (Windows) install fails with exit code 1 while the app is open |
| #58022 | open | Fleet-maintained apps: Krita (Windows) exists query never matches, so its patch policy always passes |
| #58023 | closed | Fleet-maintained apps: Smartsheet (Windows) installs into the SYSTEM profile and can't be uninstalled |
| #58024 | closed | Fleet-maintained apps: Wrike (Windows) installs into the SYSTEM profile instead of a machine-wide location |
| #58043 | closed | Fleet-maintained apps: Burp Suite Community Edition (Windows) install fails with exit code 1 while the app is open |
| #58044 | closed | Fleet-maintained apps: Ferdium (macOS) is never detected after install, so its patch policy always passes |
| #58045 | closed | Fleet-maintained apps: Vernier Spectral Analysis (Windows) is never detected, so its patch policy always passes |
| #58051 | closed | Fleet-maintained apps: NordVPN (Windows) is never detected, so its patch policy always passes |
| #58052 | closed | Fleet-maintained apps: SigmaOS and Malwarebytes for Mac (macOS) show "Update available" forever after installing the current version |
| #58069 | open | Fleet-maintained apps: grepWin (Windows) is never detected, so its patch policy always passes |
| #58070 | closed | Fleet-maintained apps: Rtools (Windows) install fails because its script stops the installer after 480 seconds |
| #58075 | closed | Fleet-maintained apps: Cursor (Windows) install fails with exit code 5 while the app is open |
| #58080 | closed | Fleet-maintained apps: Prism (macOS) can't be updated or uninstalled while it's open ("did not quit") |
| #58081 | closed | Fleet-maintained apps: ShareFile (macOS) never shows an installed version and its patch policy always fails (catalog bundle ID is a nested XPC service) |
| #58082 | closed | Fleet-maintained apps: Logi Options+ (macOS) install doesn't quit the app when it's open |
| #58084 | open | Fleet-maintained apps: Notion (Windows) shows as installed with Notion Calendar's version on hosts that only have Notion Calendar |
| #58089 | closed | Fleet-maintained apps: i1Profiler (macOS) can't be updated or uninstalled while it's open ("did not quit") |
| #58132 | closed | Fleet-maintained apps: MEGAsync (Windows) install always fails with exit code 2 |
| #58133 | closed | Fleet-maintained apps: PureVPN (macOS) can't be updated or uninstalled after Fleet installs it ("did not quit") |
| #58134 | closed | Fleet-maintained apps: Spotify (Windows) installs into the SYSTEM profile instead of the signed-in user's |
| #58135 | closed | Fleet-maintained apps: Vivaldi (Windows) install never finishes while Vivaldi is open, and later installs and uninstalls fail (exit 60) |
| #58160 | closed | Fleet-maintained apps: Pale Moon (Windows) install never finishes on Windows on ARM |
| #58173 | closed | Fleet-maintained apps: JetBrains IDEs (Windows) other than GoLand still hang on reinstall (non-empty install directory) |
| #58197 | closed | Fleet-maintained apps: Antigravity IDE (Windows) installs into the SYSTEM profile instead of the signed-in user's |
| #58198 | closed | Fleet-maintained apps: KNIME Analytics Platform (Windows) is never detected, so its patch policy always passes |
| #58227 | closed | Fleet-maintained apps: Klokki (macOS) never shows an installed version and its patch policy always fails (catalog bundle ID is its login-item helper) |
| #58228 | closed | Fleet-maintained apps: Notesnook (Windows) can't be uninstalled — every uninstall fails "The system cannot find the file specified" |
| #58319 | closed | Fleet-maintained apps: Tableau Prep (Windows) install fails with exit code 65535 while the app is open |
| #58320 | closed | Fleet-maintained apps: Fellow (Windows on ARM) can't be uninstalled, and later installs stay pending |
| #58497 | closed | Fleet-maintained apps: REDCINE-X PRO (macOS) shows version 1.0 and its patch policy always fails (catalog bundle ID is the bundled RED Tether app) |
| #58530 | closed | Fleet-maintained apps: SuperCollider (Windows) is never detected after install, so its patch policy always passes |

**4. Build changes**

No build change this month.

**5. Plan changes**

TODO: what changed in the plan this month.

**6. Close-out criteria**

1. Every failure shape (S1–S6) has an automated check whose teeth were shown on a pre-fix build or script — see the coverage plan's escape matrix
2. The whole catalog cycled at least once per platform — darwin: done, windows: done
3. One real upstream transition followed end to end (L3) — yes
4. The harness runs on GitHub Actions for a full week — not yet
5. Every finding in the month's digests is adjudicated (filed, linked, or triaged) — check the digests
