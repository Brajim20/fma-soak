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
5. Note the result on the RC's QA ticket: `L1 FMA pipeline: N/20 passed, new failures: none (rev <rev>)`.

The suite exits non-zero only on a new failure. It also runs nightly in CI (`.github/workflows/l1-pipeline.yml`) against the soak instance's revision and main; a new failure or a known-open test that starts passing opens or comments on the "L1 pipeline needs a look" issue. Running it by hand is still how to check an RC the day it's cut.

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
| test_pin_holds_on_new_release | pinning: a literal pin holds through a new release; Latest then advances to it |
| test_pin_moves_patch_policy | #49474: pinning an older version rewrites the patch policy for it |
| test_pinned_script_refresh | #53009: a pinned app still gets script fixes (known open) |
| test_unsatisfiable_caret_rejected | #53008: a caret pin nothing satisfies is rejected; `^1` and an unknown literal are the controls (known open) |

| test_gitops_adds_fma | #42260: an FMA in a fleet file is added at the catalog version |
| test_gitops_reapply_no_redownload | #52773: an unchanged re-run downloads and deletes nothing |
| test_gitops_dry_run_no_download | #54535: `--dry-run` downloads nothing and creates nothing (didn't reproduce with fixtures) |
| test_gitops_duplicate_fma_rejected | #56507: the same FMA twice in one fleet is an error (known open) |
| test_gitops_caret_unsatisfiable_rejected | #44542: an unsatisfiable caret fails the run cleanly; the server stays up |
| test_gitops_version_error_names_versions | #56508: that error points to Actions > Versions (known open) |
| test_gitops_patch_and_install_policy_same_slug | #43623 #45987: a patch and an install policy for one FMA both apply; re-run clean |
| test_gitops_swap_same_bundle_id | #55140: swapping same-bundle-ID FMAs never leaks "sql: no rows" (didn't reproduce with fixtures) |
| test_generate_gitops_roundtrip | #50694 #41390 #43770: an exported fleet file applies back with the slug and the title unchanged (drops `secrets:` first, #47820) |

GitOps cases each use their own fleet and their own installer bytes (GitOps looks installers up by hash across
all fleets). The export round trip uses a real slug (`google-chrome/darwin`) with the fixture installer, because
fleetctl reads FMA manifests from Fleet's public catalog itself.

The soak's update path covers the host side of pinning: the pinned older version is what lands on the host (#56873).

Last run: 937e874 (4.94.0-rc.2610272345), 2026-10-31: 14/20 passed, the six failures are the known open bugs.

`throwaway.sh up` creates a new database each time (`fleet_qa_pipe_<timestamp>`) and `down` leaves it in place:
databases are never dropped. The suite needs a fresh server per run (each test creates its own app once).
