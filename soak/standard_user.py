"""Phase 3: manage the standard (non-admin) account on a soak host, through Fleet scripts.

  python3 standard_user.py <create|autologin-on|autologin-off|delete> --host N
  python3 standard_user.py waf-check --host N

The scripts are hosts/standard-user/{macos.sh,windows.ps1}. Before running one, its text goes to the host inside a
wrapper that doesn't execute it (a heredoc on macOS, a here-string on Windows): qa.fleetdm.com's firewall rejects
some script text with a 403 (2026-11-28: `${env:ProgramFiles(x86)}`), and this finds out without running any of it.
Never split a script into lines to find what's blocked: a fragment run alone can delete files.

Auto-login changes who is signed in from the next boot on. Nothing here reboots the host.
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(HERE), "hosts", "standard-user")
ACTIONS = ("create", "autologin-on", "autologin-off", "delete")


def script(plat, action):
    name = "macos.sh" if plat == "darwin" else "windows.ps1"
    return open(os.path.join(SCRIPTS, name)).read().replace("__ACTION__", action)


def inert(plat, text):
    """The script as data: same text for the firewall to inspect, nothing runs."""
    if plat == "darwin":
        return f": <<'FMA_SOAK_INERT'\n{text}\nFMA_SOAK_INERT\necho inert-ok"
    assert "'@" not in text
    return f"$null = @'\n{text}\n'@\n'inert-ok'"


def passes_firewall(host, plat, action):
    st, d = R.api("POST", "/scripts/run", {"host_id": host, "script_contents": inert(plat, script(plat, action))})
    return st < 300, st


def platform(host):
    st, d = R.api("GET", f"/hosts/{host}")
    p = ((d.get("host") or {}).get("platform") or "") if st == 200 else ""
    return "darwin" if p == "darwin" else "windows" if p == "windows" else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=ACTIONS + ("waf-check",))
    ap.add_argument("--host", type=int)
    a = ap.parse_args()
    if not a.host:
        sys.exit("--host is required (waf-check runs the inert copy on that host)")
    plat = platform(a.host)
    if not plat:
        sys.exit(f"host {a.host}: not a macOS or Windows host")
    actions = ("create",) if a.action == "waf-check" else (a.action,)  # same text either way but the action word
    for act in actions:
        ok, st = passes_firewall(a.host, plat, act)
        print(f"firewall {plat} {act}: {'ok' if ok else f'BLOCKED ({st})'}")
        if not ok:
            sys.exit(1)
    if a.action == "waf-check":
        return
    res = R.run_script(a.host, script(plat, a.action), timeout=600)
    print(f"host {a.host} {a.action}: exit {res.get('exit_code')}: {(res.get('output') or res.get('error') or '').strip()}")
    sys.exit(0 if res.get("exit_code") == 0 else 1)


if __name__ == "__main__":
    main()
