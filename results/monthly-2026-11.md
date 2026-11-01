## FMA soak — November 2026 update

**Environment:** qa.fleetdm.com, fleet 65 · builds: 4.94.0-rc.2610272345 (937e87458c)

**1. Runs** — 53 cycles: pass 18, findings 20, skipped 15

**2. Coverage** — darwin 56/984 (5.7%), round 1; windows 89/459 (19.4%), round 1

**3. Escapes caught or confirmed**

| Issue | State | Title |
|---|---|---|
| #45895 | merged | DMG install script use yes and update scripts |
| #47037 | merged | Add several Windows FMAs |
| #49682 | closed | Show a clear error when adding Mozilla Firefox and Firefox ESR to the same fleet |
| #50077 | open | Windows software with version in name still matches wrong title for apps needing a custom match pattern |
| #51875 | closed | Per-user Windows Fleet-maintained apps install into SYSTEM's profile, where they are unusable and often unremovable |
| #53050 | open | Allow both x64 and ARM64 Windows FMA variants of the same app in one fleet |
| #55360 | open | Adding a Fleet-maintained app returns 500 "mismatch in maintained app SHA256 hash" with an unhelpful message |
| #55961 | open | Explore Fleet-maintained apps and build sustained FMA automation |
| #56589 | merged | Unfreeze MEGAsync (darwin) |
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

**4. Build changes**

No build change this month.

**5. Plan changes**

TODO: what changed in the plan this month.

**6. Close-out criteria**

1. Every failure shape (S1–S6) has an automated check whose teeth were shown on a pre-fix build or script — see the coverage plan's escape matrix
2. The whole catalog cycled at least once per platform — darwin: in progress, windows: in progress
3. One real upstream transition followed end to end (L3) — yes
4. The harness runs on GitHub Actions for a full week — not yet
5. Every finding in the month's digests is adjudicated (filed, linked, or triaged) — check the digests
