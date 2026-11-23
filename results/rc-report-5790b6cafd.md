# RC regression report: 4.95.0-rc.2611162253 (5790b6cafd)

Baseline: **4.94.0-rc.2610272345 (937e87458c)** · app runs on the current build: 2719 · compared on both builds: 204

**10 regression suspects** in the Fleet build (passed on the baseline, or a new failure shape, failing now, same catalog). Bugs the baseline already had aren't listed: customers have those already.

| App | Path | Why | Retry | Findings now | Triaged as |
|---|---|---|---|---|---|
| 8x8-work/darwin@858 | update_path | passed on the baseline | reproduced | uninstall: app still on host after cleanup uninstall | **new** |
| adobe-creative-cloud/windows@862 | lifecycle | passed on the baseline | reproduced | install: first install ended as failed_install<br>install: second install ended as failed_install | **new** |
| docker-desktop/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| firefox@developer-edition/windows@861 | update_path | passed on the baseline | reproduced | S4: self-service changed by pinning (#59238): True -> False | filed #59238 (open): pinning a version turns Self-service off and setting Latest turns it back on; known, stops matching when #59238 closes as fixed |
| fleet-desktop/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| microsoft-teams/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| onedrive/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| python-3.13-arm64/windows@864 | lifecycle | new failure shape S1 | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| slack/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| wezterm/darwin@865 | lifecycle | new failure shape S3, install | reproduced | S3: first uninstall: app still on host (status None)<br>install: second install ended as None<br>S3: second uninstall: app still on host (status None) | **new** |

Next: check each against the baseline's code (is the failing step in Fleet's code or the app's manifest?). Catalog causes aren't release blockers: the catalog ships on its own.

**Catalog changes (7)**: failing now, but the app's manifest or the vendor's installer changed between the two runs, so the build isn't the suspect. Not release blockers (the catalog ships on its own); they still go through triage.

| App | Path | What changed | Findings now | Triaged as |
|---|---|---|---|---|
| 1password/windows@861 | lifecycle | 8.12.44 -> 8.12.46 | S2: the Application event log has errors naming the app after Fleet installed and opened it: 1000 Application Error: Faulting application name: 1Password.exe, version: 8.12.46.29, time stamp: 0x6adec14a Faulting module name: index.node, version: 0.0.0.0, time stamp: 0x6afc2ee0 Exception code: 0xc000 | **new** |
| 1password/windows@862 | lifecycle | 8.12.44 -> 8.12.46 | S2: the Application event log has errors naming the app after Fleet installed and opened it: 1000 Application Error: Faulting application name: 1Password.exe, version: 8.12.46.29, time stamp: 0x6adec14a Faulting module name: index.node, version: 0.0.0.0, time stamp: 0x6afc2ee0 Exception code: 0xc000 | **new** |
| adobe-acrobat-pro/windows@859 | lifecycle | 26.002.22031 -> 26.003.22110 | S1: inventory never reported the app after install + refetch | dup #57139 (commented 2026-11-19): host reports upgrade_code {AC76BA86-0000-0000-7760-7E8A45000000}, manifest/title has none, install lands on the upgrade-code-keyed 'Adobe Acrobat (64-bit)' title (co |
| chatgpt/darwin@860 | lifecycle | 26.1111.21308 -> 26.1111.71025 | S3: first uninstall: app still on host (status None) | already filed #57025: ChatGPT reports com.openai.codex while the FMA's unique_identifier stays com.openai.chat (decided in #55246), so inventory lands on a separate 'Codex' title; same on 4.94 and 4.9 |
| claude/darwin@858 | lifecycle | 5.11492.5 -> 6.6395.1 | S1: inventory never reported the app after install + refetch | **new** |
| firefox/darwin@858 | lifecycle | 160.0 -> 160.0.1 | S1: inventory never reported the app after install + refetch | **new** |
| google-chrome/darwin@858 | lifecycle | 157.0.8103.79 -> 158.0.8127.37 | S1: inventory never reported the app after install + refetch | **new** |

Fixed on this build (14): 1password/windows@864 (lifecycle), acorn/darwin@860 (lifecycle), bitwarden/windows@862 (lifecycle), brave-browser/windows@862 (lifecycle), db-browser-for-sqlite/windows@864 (lifecycle), druva-insync/darwin@858 (lifecycle), eclipse-temurin-jre-17/windows@862 (lifecycle), google-chrome/windows@861 (lifecycle), google-chrome/windows@861 (update_path), hides/darwin@865 (lifecycle), rubymine/windows@861 (lifecycle), transmission/windows@859 (lifecycle), whispering/darwin@865 (lifecycle), zoom/darwin@858 (update_path)
