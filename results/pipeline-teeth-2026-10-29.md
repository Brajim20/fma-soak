# L1 suite: proof each test fails on the build before its fix — 2026-10-29

| Test | Pre-fix build (parent of fix PR) | Pre-fix result | Current 937e874 |
|---|---|---|---|
| test_newest_version_active (#50968) | 8a8707931a (before #51071) | FAIL — active stayed 151.0.7922.76 | PASS — 151.0.7922.109 |
| test_script_only_refresh (#51577) | c4ad80a254 (before #52057) | FAIL — install script stayed "A" | PASS — "B" |
| test_admin_script_edit_kept (#51577 guard) | c4ad80a254 | PASS (guard, not a repro) | PASS |
| test_no_redownload_hashless (#53475) | 153d8e440e (before #55150) | FAIL — 2 installer GETs over 2 passes | PASS — 0 |
| test_hash_mismatch_error (#55360) | — | — | FAIL — 500 (bug open) |
| test_download_failure_error (#56891) | — | — | FAIL — 408 + raw `dial tcp: lookup …` (bug open; 500 on qa.fleetdm.com) |

Binaries: pipeline/bin/fleet-<rev>, fleetctl-<rev>. Run: `./throwaway.sh up <rev> && PIPE_BUILD=<rev> python3 suite.py [-k name] && ./throwaway.sh down`.
