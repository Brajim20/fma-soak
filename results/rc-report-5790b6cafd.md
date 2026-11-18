# RC regression report: 4.95.0-rc.2611162253 (5790b6cafd)

Baseline: **4.94.0-rc.2610272345 (937e87458c)** · app runs on the current build: 396 · compared on both builds: 104

**1 regression suspect** (passed on the baseline, or a new failure shape, and failing now). Bugs the baseline already had aren't listed: customers have those already.

| App | Path | Why | Retry | Findings now | Triaged as |
|---|---|---|---|---|---|
| firefox/darwin@858 | update_path | passed on the baseline | reproduced | S2: upgrade to 160.0: executable owner 501:20, expected 0:80 (Firefox's updater needs root:<user> + group-writable (#55093))<br>S2: upgrade to 160.0: executable mode 0755, needs at least 0775 (Firefox's updater needs root:<user> + group-writable (#55093))<br>S2: executable ownership/mode changed by the upgrade: 0:80 0775 -> 501:20 0755 (#55093 shape) | **new** |

Next: check each against the baseline's code (is the failing step in Fleet's code or the app's manifest?). Catalog causes aren't release blockers: the catalog ships on its own.

Fixed on this build (6): figma/darwin@858 (lifecycle), google-chrome/windows@861 (lifecycle), microsoft-defender/darwin@858 (lifecycle), rubymine/windows@861 (lifecycle), whispering/darwin@865 (lifecycle), zoom/darwin@858 (update_path)
