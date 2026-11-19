# RC regression report: 4.95.0-rc.2611162253 (5790b6cafd)

Baseline: **4.94.0-rc.2610272345 (937e87458c)** · app runs on the current build: 663 · compared on both builds: 109

**1 regression suspect** in the Fleet build (passed on the baseline, or a new failure shape, failing now, same catalog). Bugs the baseline already had aren't listed: customers have those already.

| App | Path | Why | Retry | Findings now | Triaged as |
|---|---|---|---|---|---|
| wezterm/darwin@865 | lifecycle | new failure shape S3, install | reproduced | S3: first uninstall: app still on host (status None)<br>install: second install ended as None<br>S3: second uninstall: app still on host (status None) | **new** |

Next: check each against the baseline's code (is the failing step in Fleet's code or the app's manifest?). Catalog causes aren't release blockers: the catalog ships on its own.

**Catalog changes (2)**: failing now, but the app's manifest or the vendor's installer changed between the two runs, so the build isn't the suspect. Not release blockers (the catalog ships on its own); they still go through triage.

| App | Path | What changed | Findings now | Triaged as |
|---|---|---|---|---|
| chatgpt/darwin@860 | lifecycle | 26.1111.21308 -> 26.1111.71025 | S3: first uninstall: app still on host (status None) | already filed #57025: ChatGPT reports com.openai.codex while the FMA's unique_identifier stays com.openai.chat (decided in #55246), so inventory lands on a separate 'Codex' title; same on 4.94 and 4.9 |
| spotify/windows@862 | lifecycle | the vendor's installer no longer matches the manifest's hash | add: add failed 500: mismatch in maintained app SHA256 hash | catalog race, fixed: SpotifyFullSetupX64.exe (unversioned URL) served 1.3.10 while the catalog still had 1.3.9's hash; catalog moved to 1.3.10 in #59164 (2026-11-18 20:33Z). The 500 instead of a 4xx i |

Fixed on this build (5): druva-insync/darwin@858 (lifecycle), google-chrome/windows@861 (lifecycle), rubymine/windows@861 (lifecycle), whispering/darwin@865 (lifecycle), zoom/darwin@858 (update_path)
