# L1 regressions vs the release — 2026-11-18

Baseline: **4.95.0-rc.2611162253 (5790b6cafd)** (15/21 pass)
- **rc** 4.95.0-rc.2611162253 (5790b6cafd): no regressions
- **main** main (e6871c46f1): **test_admin_script_edit_kept**, **test_gitops_adds_fma**, **test_newest_version_active**, **test_patch_policy_follows_promotion**, **test_pin_holds_on_new_release**, **test_pin_uncached_version_rejected**, **test_script_only_refresh**

Each one passed on the release: a release blocker until shown otherwise (check the test isn't flaky by re-running the target, then find the commit with git bisect between the two revisions).
