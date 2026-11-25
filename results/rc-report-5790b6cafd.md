# RC regression report: 4.95.0-rc.2611162253 (5790b6cafd)

Baseline: **4.94.0-rc.2610272345 (937e87458c)** · app runs on the current build: 3471 · compared on both builds: 256

**No regression suspects** in the Fleet build (passed on the baseline, or a new failure shape, failing now, same catalog). Bugs the baseline already had aren't listed: customers have those already.

**Catalog changes (11)**: failing now, but the app's manifest or the vendor's installer changed between the two runs, so the build isn't the suspect. Not release blockers (the catalog ships on its own); they still go through triage.

| App | Path | What changed | Findings now | Triaged as |
|---|---|---|---|---|
| 1password/windows@861 | lifecycle | 8.12.44 -> 8.12.46 | S2: the Application event log has errors naming the app after Fleet installed and opened it: 1000 Application Error: Faulting application name: 1Password.exe, version: 8.12.46.29, time stamp: 0x6adec14a Faulting module name: index.node, version: 0.0.0.0, time stamp: 0x6afc2ee0 Exception code: 0xc000 | not a bug: 1Password's own index.node faults (c0000409, same offset) on all x64 hosts incl. 859 (2026-11-20) and on 8.12.44 too; every Fleet step passes (installed, inventory 8.12.46, patched passes). |
| 1password/windows@862 | lifecycle | 8.12.44 -> 8.12.46 | S2: the Application event log has errors naming the app after Fleet installed and opened it: 1000 Application Error: Faulting application name: 1Password.exe, version: 8.12.46.29, time stamp: 0x6adec14a Faulting module name: index.node, version: 0.0.0.0, time stamp: 0x6afc2ee0 Exception code: 0xc000 | not a bug: 1Password's own index.node faults (c0000409, same offset) on all x64 hosts incl. 859 (2026-11-20) and on 8.12.44 too; every Fleet step passes (installed, inventory 8.12.46, patched passes). |
| adobe-acrobat-pro/windows@859 | lifecycle | 26.002.22031 -> 26.003.22110 | S1: inventory never reported the app after install + refetch | dup #57139 (commented 2026-11-19): host reports upgrade_code {AC76BA86-0000-0000-7760-7E8A45000000}, manifest/title has none, install lands on the upgrade-code-keyed 'Adobe Acrobat (64-bit)' title (co |
| chatgpt/darwin@860 | lifecycle | 26.1111.21308 -> 26.1111.71025 | S3: first uninstall: app still on host (status None) | already filed #57025: ChatGPT reports com.openai.codex while the FMA's unique_identifier stays com.openai.chat (decided in #55246), so inventory lands on a separate 'Codex' title; same on 4.94 and 4.9 |
| claude/darwin@858 | lifecycle | 5.11492.5 -> 6.6395.1 | S1: inventory never reported the app after install + refetch | **new** |
| datadog-agent/windows@862 | lifecycle | 7.85.2.0 -> 7.85.4.0 | S2: the Application event log has errors naming the app after Fleet installed and opened it: 8 datadog-trace-agent: The Service failed: could not build arguments for function "github.com/DataDog/datadog-agent/cmd/trace-agent/subcommands/run".runTraceAgentProcess.func4 (github.com/DataDog/datadog-age | **new** |
| evernote/windows@859 | lifecycle | 11.42.3 -> 11.43.4 | S6: reinstall over the open app: the install over the open app ended as failed_install | **new** |
| evernote/windows@862 | lifecycle | 11.42.3 -> 11.43.4 | S6: reinstall over the open app: the install over the open app ended as failed_install | **new** |
| firefox/darwin@858 | lifecycle | 160.0 -> 160.0.1 | S1: inventory never reported the app after install + refetch | **new** |
| google-chrome/darwin@858 | lifecycle | 157.0.8103.79 -> 158.0.8127.37 | S1: inventory never reported the app after install + refetch | **new** |
| nessus-agent/windows@861 | lifecycle | 11.2.3.20022 -> 11.2.4.20033 | S2: the Application event log has errors naming the app after Fleet installed and opened it: 10007 Microsoft-Windows-RestartManager: Application or service 'Tenable Nessus Agent' could not be restarted. | **new** |

**Changed on the same build (6)**: passed on this build first, failing since, so the build isn't what changed (the host, the vendor or timing did). Not release blockers; they still go through triage.

| App | Path | Last pass on this build | Findings now | Triaged as |
|---|---|---|---|---|
| 8x8-work/darwin@858 | update_path | 2026-11-17T19:56 | uninstall: app still on host after cleanup uninstall | **new** |
| docker-desktop/darwin@858 | lifecycle | 2026-11-19T02:15 | S1: inventory never reported the app after install + refetch | **new** |
| fleet-desktop/darwin@858 | lifecycle | 2026-11-19T01:40 | S1: inventory never reported the app after install + refetch | **new** |
| microsoft-teams/darwin@858 | lifecycle | 2026-11-18T21:16 | S1: inventory never reported the app after install + refetch | **new** |
| onedrive/darwin@858 | lifecycle | 2026-11-18T09:37 | S1: inventory never reported the app after install + refetch | **new** |
| slack/darwin@858 | lifecycle | 2026-11-17T21:59 | S1: inventory never reported the app after install + refetch | **new** |

**No baseline for the check (6)**: every finding comes from a check added after the baseline's last run of the app, so the baseline couldn't show it. Not release blockers; they still go through triage.

| App | Path | Check | Findings now | Triaged as |
|---|---|---|---|---|
| claude/windows@861 | update_path | self-service flag across pin/unpin (a999477c) | S4: self-service changed by pinning (#59238): True -> False | filed #59238 (open): pinning a version turns Self-service off and setting Latest turns it back on; known, stops matching when #59238 closes as fixed |
| power-automate/windows@862 | lifecycle | Windows event-log scan (a999477c) | S2: the Application event log has errors naming the app after Fleet installed and opened it: 1000 Application Error: Faulting application name: PAD.Java.Installer.Host.exe, version: 2.74.191.26302, time stamp: 0x6ada0000 Faulting module name: KERNELBASE.dll, version: 10.0.26100.9974, time stamp: 0xa | not a bug: Power Automate's own PAD.Java.Installer.Host.exe throws an unhandled .NET exception (0xe0434352, KERNELBASE.dll) after it opens, on all x64 hosts (859/861/862, 2026-11-23/24); every Fleet s |
| signal/windows@861 | update_path | self-service flag across pin/unpin (a999477c) | S4: self-service changed by pinning (#59238): True -> False | filed #59238 (open): pinning a version turns Self-service off and setting Latest turns it back on; known, stops matching when #59238 closes as fixed |
| slack/windows@861 | update_path | self-service flag across pin/unpin (a999477c) | S4: self-service changed by pinning (#59238): True -> False | filed #59238 (open): pinning a version turns Self-service off and setting Latest turns it back on; known, stops matching when #59238 closes as fixed |
| sublime-text/windows@861 | update_path | self-service flag across pin/unpin (a999477c) | S4: self-service changed by pinning (#59238): True -> False | filed #59238 (open): pinning a version turns Self-service off and setting Latest turns it back on; known, stops matching when #59238 closes as fixed; not a bug: the 'older' pin 4.0.0.421500 is the sam |
| zoom/windows@861 | update_path | self-service flag across pin/unpin (a999477c) | S4: self-service changed by pinning (#59238): True -> False | filed #59238 (open): pinning a version turns Self-service off and setting Latest turns it back on; known, stops matching when #59238 closes as fixed |

**Already on the baseline on another host (3)**: the baseline showed the same failure for this app on another host with the same CPU; the baseline run on this host stopped earlier or got lucky. Not new in this build; they still go through triage.

| App | Path | Baseline showed it on | Findings now | Triaged as |
|---|---|---|---|---|
| adobe-creative-cloud/windows@862 | lifecycle | 635 | install: first install ended as failed_install<br>install: second install ended as failed_install | **new** |
| python-3.13-arm64/windows@864 | lifecycle | 629, 863 | S1: inventory never reported the app after install + refetch | **new** |
| wezterm/darwin@865 | lifecycle | 631 | S3: first uninstall: app still on host (status None)<br>install: second install ended as None<br>S3: second uninstall: app still on host (status None) | not a bug (documented limitation): WezTerm.app ships a placeholder CFBundleShortVersionString 0.1.0 and CFBundleVersion 1 in every release, so osquery always reports 0.1.0; PR #53892 (adds the FMA) do |

Fixed on this build (17): 1password/windows@864 (lifecycle), acorn/darwin@860 (lifecycle), bitwarden/windows@862 (lifecycle), brave-browser/windows@862 (lifecycle), clop/darwin@860 (lifecycle), db-browser-for-sqlite/windows@864 (lifecycle), druva-insync/darwin@858 (lifecycle), druva-insync/darwin@860 (lifecycle), eclipse-temurin-jre-17/windows@862 (lifecycle), google-chrome/windows@861 (lifecycle), google-chrome/windows@861 (update_path), hides/darwin@865 (lifecycle), rubymine/windows@861 (lifecycle), transmission/darwin@865 (lifecycle), transmission/windows@859 (lifecycle), whispering/darwin@865 (lifecycle), zoom/darwin@858 (update_path)
