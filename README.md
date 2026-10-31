# fma-soak

Sustained testing of [Fleet](https://github.com/fleetdm/fleet)-maintained apps (FMA): real installs, upgrades and
uninstalls on real macOS and Windows hosts, driven through a Fleet instance every 6 hours, plus a daily audit of
the FMA catalog. Built for [fleetdm/fleet#55961](https://github.com/fleetdm/fleet/issues/55961).

Status: proof of concept. It runs here, on GitHub Actions, before moving into fleetdm/fleet.

## What it checks

| Layer | What | Runs |
|---|---|---|
| L0 | Audit of every manifest in `ee/maintained-apps/outputs` (dead URLs, hashes, versions behind upstream, Windows exists queries vs the name the installer registers), then a Fleet-side confirm of each suspect | daily |
| L1 | Update-pipeline suite against a throwaway Fleet server (`pipeline/`): the revision the soak instance runs, and fleetdm/fleet main (`.github/workflows/l1-pipeline.yml`) | nightly |
| L2 | Lifecycle on the soak hosts: add → install → inventory and patch policy → uninstall → reinstall, plus upgrading from a pinned previous version. With the app open: a reinstall over the open app (every cycle), and the upgrade on alternate days | every 6 h |
| L2 | Patch when closed: Fleet's own patch policy with "patch when closed" on one app a day per platform, over several passes: every attempt while the app is open must be a skip reported as one; after it's closed the patch must land without reopening it | daily |
| L3 | Watcher: a few apps stay installed with patch policies, and each real upstream release is followed until it reaches the host (hours the host was offline don't count) | every 2 h |

Failures are grouped by shape, not by app:

| Shape | Meaning |
|---|---|
| S1 version identity | published, installed, inventory and patch-policy versions disagree |
| S2 install context | wrong user, location, ownership or permissions |
| S3 reported ≠ real | Fleet's status disagrees with what's on the host |
| S4 update pipeline | active version, pinning, script refresh, re-download, hash handling |
| S5 catalog | stale, duplicate or broken manifest (L0) |
| S6 app open | installed or updated while the app was open: replaced under it, not reopened, opened when it was closed, or doesn't start after |
| add / install / uninstall | the step itself failed; needs a look before it gets a shape |

## Rules it keeps

- It works in **one fleet** on the instance and touches only that fleet and its soak hosts: no global settings,
  no other fleets.
- It never touches apps it didn't install, never removes titles that were already in the fleet, and always
  restores a pinned title to Latest.
- A finding is re-run once before it's reported; the digest says whether it reproduced.
- Nothing is posted to GitHub automatically. Findings land in a daily digest for a person to triage.
- Saved output is scrubbed of local account names before it's stored.

## Running it on GitHub Actions

`.github/workflows/fma-soak.yml`: back-to-back soak runs. Each run is a 5 h pass on both hosts side by side, a watch
job beside it (hosts checked every 30 min, residents every 2 h, the catalog audit once a day), then one digest, and
it starts the next run when it ends. GitHub's cron skips and delays runs on a quiet repo, so the crons are only a
backup that restarts the chain if it breaks. A host offline for 2 h opens (or comments on) a "Soak host offline"
issue here, which emails the repo owner. State and results are kept on the `results` branch; screenshots and logs
are run artifacts (30 days). Stop everything by disabling the workflow; set the variable `FMA_SOAK_CHAIN=off` to
keep only the cron.

Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Variable | `FLEET_URL` | the Fleet instance |
| Variable | `FMA_SOAK_FLEET_ID` | the soak fleet |
| Variable | `FMA_SOAK_HOST_DARWIN`, `FMA_SOAK_HOST_WINDOWS` | the soak hosts' IDs |
| Secret (environment `fma-soak`) | `FMA_SOAK_TOKEN` | API-only user, Maintainer on the soak fleet only |
| Secret (environment `fma-soak`) | `FMA_SOAK_UI_EMAIL`, `FMA_SOAK_UI_PASSWORD` | optional: Observer on the soak fleet only, for screenshots |

The soak hosts are ordinary enrolled machines (VMs work) that must stay awake and online; the runner only talks to
the Fleet instance. **Don't run the workflow and a local loop against the same hosts at the same time.**

## Running it locally

Python 3.10+ with PyYAML, zsh, and a fleetdm/fleet checkout for the catalog.

```
cp config.local.example.json config.local.json   # instance, fleet, hosts, catalog path
export FMA_SOAK_TOKEN=...                         # or a fleetctl context named in config.local.json

cd soak
python3 runner.py firefox/darwin                  # one lifecycle cycle
python3 runner.py firefox/darwin --update         # upgrade from the previous version
python3 sample.py --platform windows --count 3 --dry-run   # what the sample would pick next
python3 loop.py --once                            # one full pass on both hosts
python3 digest.py                                 # today's digest → results/digest-<date>.md
```

`soak/loop.py` without `--once` runs a pass every 6 h; on a Mac it can run as a launchd job.

## Reading the digest

`results/digest-<date>.md`. Only **Needs a look** (and any confirm line marked **new, needs a look**) needs
action. Each new item carries a **context** line: the app's manifest, input and scripts, the last commits to it, issues
that mention it, and the Fleet code behind the failing step at the build it ran on. It's the starting point for
checking the behavior isn't intended, not a verdict. New items also open (or comment on) a "New FMA soak findings
<date>" issue in this repo, once per app and failure shape per day, so they reach the owner by email. For each item, open its record under `results/soak/<date>/`, then take the first that fits:

1. **Harness**: the runner judged wrong. Fix it and add a `triage.json` entry with `before` set to the fix time.
2. **Environment**: caused by the soak setup (Windows on ARM, no nested virtualization, a slow link). Add a
   `triage.json` entry; if it always happens on that host, also `known_environment` in `config.json`.
3. **Flaky**: clean on retry. Once is noise; the same app across several days is a finding.
4. **Catalog / upstream**: Fleet behaves correctly but one app's manifest or vendor is wrong. Report it on
   fleetdm/fleet#55961.
5. **Fleet bug**: the cause is in Fleet (server, fleetd, generated scripts, caching, pinning, reporting). Search
   fleetdm/fleet issues first; then file or comment, and add a `triage.json` entry with the issue number.

Check it isn't intended first: UI copy, docs, the sibling path that does the same thing, existing issues.

## Layout

```
settings.py        config.json + config.local.json + environment variables
config.json        canaries, known environment, loop and sample settings (nothing instance-specific)
triage.json        adjudicated findings, which the digest moves out of "Needs a look"
soak/              runner.py (cycle, update path), sample.py, watcher.py, scenarios.py, loop.py, pass.sh,
                   digest.py, monthly.py (draft of the monthly #55961 update), tools/shot.mjs (screenshots),
                   probes/ (one-off diagnostics)
audit/             L0 audit, confirm, name check, run-daily.sh
pipeline/          L1 suite on a throwaway server, build.sh, RC_CHECKLIST.md
ci/results.sh      keeps CI state on the results branch
hosts/             soak-host setup: utm-autostart/ (launchd agent that keeps the UTM VM running)
results/           (not on main) digests, records, coverage ledger, harness state
```
