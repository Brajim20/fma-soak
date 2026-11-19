# RC regression report: 4.95.0-rc.2611162253 (5790b6cafd)

Baseline: **4.94.0-rc.2610272345 (937e87458c)** · app runs on the current build: 598 · compared on both builds: 127

**2 regression suspects** (passed on the baseline, or a new failure shape, and failing now). Bugs the baseline already had aren't listed: customers have those already.

| App | Path | Why | Retry | Findings now | Triaged as |
|---|---|---|---|---|---|
| chatgpt/darwin@860 | lifecycle | new failure shape S1, S3 | reproduced | S1: inventory never reported the app after install + refetch<br>S3: first uninstall: app still on host (status None) | **new** |
| spotify/windows@862 | lifecycle | passed on the baseline | reproduced | add: add failed 500: mismatch in maintained app SHA256 hash | **new** |

Next: check each against the baseline's code (is the failing step in Fleet's code or the app's manifest?). Catalog causes aren't release blockers: the catalog ships on its own.

Fixed on this build (7): druva-insync/darwin@858 (lifecycle), figma/darwin@858 (lifecycle), google-chrome/windows@861 (lifecycle), microsoft-defender/darwin@858 (lifecycle), rubymine/windows@861 (lifecycle), whispering/darwin@865 (lifecycle), zoom/darwin@858 (update_path)
