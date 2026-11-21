# RC regression report: 4.95.0-rc.2611162253 (5790b6cafd)

Baseline: **4.94.0-rc.2610272345 (937e87458c)** · app runs on the current build: 1677 · compared on both builds: 139

**8 regression suspects** in the Fleet build (passed on the baseline, or a new failure shape, failing now, same catalog). Bugs the baseline already had aren't listed: customers have those already.

| App | Path | Why | Retry | Findings now | Triaged as |
|---|---|---|---|---|---|
| 8x8-work/darwin@858 | update_path | passed on the baseline | reproduced | uninstall: app still on host after cleanup uninstall | **new** |
| adobe-creative-cloud/windows@862 | lifecycle | passed on the baseline | reproduced | install: first install ended as failed_install<br>install: second install ended as failed_install | **new** |
| docker-desktop/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| fleet-desktop/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| microsoft-teams/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| onedrive/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| slack/darwin@858 | lifecycle | passed on the baseline | reproduced | S1: inventory never reported the app after install + refetch | **new** |
| wezterm/darwin@865 | lifecycle | new failure shape S3, install | reproduced | S3: first uninstall: app still on host (status None)<br>install: second install ended as None<br>S3: second uninstall: app still on host (status None) | **new** |

Next: check each against the baseline's code (is the failing step in Fleet's code or the app's manifest?). Catalog causes aren't release blockers: the catalog ships on its own.

**Catalog changes (6)**: failing now, but the app's manifest or the vendor's installer changed between the two runs, so the build isn't the suspect. Not release blockers (the catalog ships on its own); they still go through triage.

| App | Path | What changed | Findings now | Triaged as |
|---|---|---|---|---|
| adobe-acrobat-pro/windows@859 | lifecycle | 26.002.22031 -> 26.003.22110 | S1: inventory never reported the app after install + refetch | dup #57139 (commented 2026-11-19): host reports upgrade_code {AC76BA86-0000-0000-7760-7E8A45000000}, manifest/title has none, install lands on the upgrade-code-keyed 'Adobe Acrobat (64-bit)' title (co |
| chatgpt/darwin@860 | lifecycle | 26.1111.21308 -> 26.1111.71025 | S3: first uninstall: app still on host (status None) | already filed #57025: ChatGPT reports com.openai.codex while the FMA's unique_identifier stays com.openai.chat (decided in #55246), so inventory lands on a separate 'Codex' title; same on 4.94 and 4.9 |
| claude/darwin@858 | lifecycle | 5.11492.5 -> 6.6395.1 | S1: inventory never reported the app after install + refetch | **new** |
| firefox/darwin@858 | lifecycle | 160.0 -> 160.0.1 | S1: inventory never reported the app after install + refetch | **new** |
| google-chrome/darwin@858 | lifecycle | 157.0.8103.79 -> 158.0.8127.37 | S1: inventory never reported the app after install + refetch | **new** |
| wireshark/windows@859 | lifecycle | 4.6.9 -> 4.6.10 | add: add failed 524: error code: 524
 | #56971 class (confirmed on 629 2026-10-30): publisher registered with a trailing '/', query has none; comment drafted |

Fixed on this build (9): bitwarden/windows@862 (lifecycle), brave-browser/windows@862 (lifecycle), db-browser-for-sqlite/windows@864 (lifecycle), druva-insync/darwin@858 (lifecycle), google-chrome/windows@861 (lifecycle), google-chrome/windows@861 (update_path), rubymine/windows@861 (lifecycle), whispering/darwin@865 (lifecycle), zoom/darwin@858 (update_path)
