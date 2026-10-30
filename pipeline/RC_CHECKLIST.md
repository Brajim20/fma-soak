# FMA update-pipeline check for each RC (#55961, task 2.8)

Run before signing off a release candidate. About 5 minutes once the binaries exist; the suite itself takes
under 30 seconds. Everything runs on a local throwaway server, never on qa.fleetdm.com.

1. Find the RC's revision: `curl -s https://<rc-instance>/version` → `revision` (first 7–10 characters).
2. Build it (skip if `bin/fleet-<rev>` exists; copied binaries have vanished before, so check):
   `./build.sh <rev>`
3. Run the suite:
   `./throwaway.sh up <rev> && PIPE_BUILD=<rev> python3 suite.py; ./throwaway.sh down`
4. Read `../results/pipeline-<date>-<rev>.md`:
   - **New failures: none** → the FMA update pipeline is fine on this RC.
   - A **FAIL** row → a regression in the update pipeline. Reproduce it on the RC instance and file it
     against the RC (unreleased bug, RC milestone).
   - **KNOWN #NNNNN** rows fail by design until that bug is fixed.
   - **PASS (was failing for #NNNNN: retest it)** → that bug looks fixed on this RC. Retest it with
     `fleet-bug-retest`, then remove the test from `KNOWN_OPEN` in `suite.py`.
5. Note the result on the RC's QA ticket: `L1 FMA pipeline: N/7 passed, new failures: none (rev <rev>)`.

The suite exits non-zero only on a new failure, so it can gate a CI job later (task 4.9).

## What it covers

| Test | Escape |
|---|---|
| test_newest_version_active | #50968: newest of 4-part versions becomes active |
| test_script_only_refresh | #51577: a script-only manifest change reaches the stored script |
| test_admin_script_edit_kept | #51577: an admin-edited script isn't overwritten |
| test_no_redownload_hashless | #53475: unchanged hashless app isn't re-downloaded |
| test_hash_mismatch_error | #55360: hash mismatch on add → 4xx + plain message (known open) |
| test_download_failure_error | #56891: dead download host on add → 4xx + plain message (known open) |
| test_patch_policy_follows_promotion | 4.3: promotion rewrites the patch policy query and resets it |

Last run: 937e874 (4.94.0-rc.2610272345), 2026-10-30: 5/7 passed, the two failures are the known open bugs.
