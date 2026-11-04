## FMA soak — November 2026 update

**Environment:** qa.fleetdm.com, fleet 65 · builds: 4.94.0-rc.2610272345 (937e87458c)

**1. Runs** — 595 cycles: pass 247, findings 151, flaky 2, environment 12, harness 1, skipped 182

**2. Coverage** — darwin 97/973 (10.0%), round 1; windows 245/461 (53.1%), round 2

**3. Escapes caught or confirmed**

| Issue | State | Title |
|---|---|---|
| #42082 | open | Windows FMAs: Support multiple architectures |
| #45895 | merged | DMG install script use yes and update scripts |
| #47037 | merged | Add several Windows FMAs |
| #49682 | closed | Show a clear error when adding Mozilla Firefox and Firefox ESR to the same fleet |
| #50077 | open | Windows software with version in name still matches wrong title for apps needing a custom match pattern |
| #50937 | open | Allow multi-file software installers via archive extraction |
| #51793 | open | Build dependency checking and installation into Windows FMAs |
| #51875 | closed | Per-user Windows Fleet-maintained apps install into SYSTEM's profile, where they are unusable and often unremovable |
| #53050 | open | Allow both x64 and ARM64 Windows FMA variants of the same app in one fleet |
| #55360 | open | Adding a Fleet-maintained app returns 500 "mismatch in maintained app SHA256 hash" with an unhelpful message |
| #55961 | open | Explore Fleet-maintained apps and build sustained FMA automation |
| #56589 | merged | Unfreeze MEGAsync (darwin) |
| #56802 | open | Add ARM64 Windows Fleet-maintained apps for 23 popular apps |
| #56873 | open | Fleet-maintained apps: pinning a hashless app to an older version installs the latest version |
| #56891 | open | Fleet-maintained apps: a failed installer download returns 500 with the raw network error |
| #56948 | open | Windows uninstall reports success when the app wasn't installed by its MSI, and the app stays installed |
| #56971 | open | Fleet-maintained apps: AWS CLI (Windows) is never detected, so its patch policy always passes |
| #57008 | open | Fleet-maintained apps: MEGAsync (macOS) patch policy fails on up-to-date hosts |
| #57025 | open | macOS `ChatGPT.app` (bundle id `com.openai.codex`) is listed as "Codex" in software inventory — search for "ChatGPT" misses it; three different "Codex" titles on the Software page |
| #57051 | open | Druva InSync (GovCloud) colliding with Druva InSync |
| #57113 | open | Fleet-maintained apps: REAPER (Windows) is never detected, so its patch policy always passes |
| #57114 | open | Fleet-maintained apps: Arduino IDE (Windows) install always fails with 1603 (per-user MSI under SYSTEM hits the 260-character path limit) |
| #57116 | open | Fleet-maintained apps: Evernote (macOS) installs a 2024 build (10.105.4) labeled as 11.40.3 |
| #57118 | open | Fleet-maintained apps: Sublime Text (Windows) registers no version, so its patch policy fails on every host |
| #57119 | open | Fleet-maintained apps: Sequel Ace (macOS) detection uses the wrong bundle ID, so installs land on a separate title |
| #57120 | open | Fleet-maintained apps: JASP (macOS) detection uses an outdated bundle ID (org.jasp-stats.JASP → org.jaspstats.jasp) |
| #57136 | open | Fleet-maintained apps: Galaxy Modeler (Windows) is never detected, so its patch policy always passes |
| #57137 | open | Fleet-maintained apps: PostgreSQL 18 (Windows) is never detected (installer registers "PostgreSQL 18 " with a trailing space) |
| #57138 | open | Fleet-maintained apps: PostgreSQL 18 (Windows) can't be reinstalled after an uninstall, and the uninstall is reported as failed |
| #57139 | open | Fleet-maintained apps: Azul Zulu JRE 25 (Windows) installs land on a separate title (MSI upgrade code skips name match) |
| #57148 | open | fleetd: a timed-out script's child processes keep running, and on macOS an orphaned installer blocks every later package install |
| #57149 | open | Fleet-maintained apps: 010 Editor (Windows) install fails with exit code 5 while the app is open |
| #57152 | open | Fleet-maintained apps: ocenaudio and Zettlr (Windows) install into the SYSTEM profile and can't be uninstalled |
| #57153 | open | Fleet-maintained apps: cmux (macOS) patch policy fails right after installing the current version (0.65.2.2 vs 0.65.2) |
| #57157 | open | Fleet-maintained apps: Snagit (Windows) is never detected, so its patch policy always passes |
| #57190 | merged | Regenerate macOS zip FMA outputs for ditto extraction (2/4) |
| #57224 | open | Fleet-maintained apps: Evernote (Windows) is never detected, so its patch policy always passes |
| #57225 | open | Fleet-maintained apps: AnyBurn (Windows) patch policy fails right after installing the current version (6.9.0.0 vs 6.9) |
| #57321 | open | Fleet-maintained apps: Webex (Windows) is never detected, so its patch policy always passes |
| #57333 | open | Fleet-maintained apps: dupeGuru (Windows) is never detected, so its patch policy always passes |
| #57334 | open | Fleet-maintained apps: Plantronics Hub (Windows) is never detected, so its patch policy always passes |
| #57335 | open | Fleet-maintained apps: Ollama (Windows) install always fails with exit code 1 |
| #57336 | open | Fleet-maintained apps: Steam (macOS) can't be added: 422 Validation Failed |
| #57337 | open | Fleet-maintained apps: Wondershare Filmora (macOS) is never detected (bundle ID com.wondershare.filmoramac vs filmoramacos) |
| #57338 | open | Fleet-maintained apps: WeChat (macOS) patch policy fails right after installing the current version (4.2.0.16 vs 4.2.0) |
| #57341 | open | Fleet-maintained apps: BitBox (macOS) is never detected (bundle ID ch.shiftcrypto.BitBoxApp vs ch.shiftcrypto.wallet) |
| #57390 | open | Fleet-maintained apps: Google Drive (Windows) can't be installed again after Fleet uninstalls it (exit 1610) |
| #57391 | open | Fleet-maintained apps: MonitorControl (macOS) never detected after install (bundle ID mismatch) |

**4. Build changes**

No build change this month.

**5. Plan changes**

TODO: what changed in the plan this month.

**6. Close-out criteria**

1. Every failure shape (S1–S6) has an automated check whose teeth were shown on a pre-fix build or script — see the coverage plan's escape matrix
2. The whole catalog cycled at least once per platform — darwin: in progress, windows: done
3. One real upstream transition followed end to end (L3) — yes
4. The harness runs on GitHub Actions for a full week — not yet
5. Every finding in the month's digests is adjudicated (filed, linked, or triaged) — check the digests
