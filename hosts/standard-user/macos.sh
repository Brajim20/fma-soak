#!/bin/sh
# fma-soak phase 3: a standard (non-admin) local account, so the soak sees what customer fleets have at the console.
# Run as root through Fleet (soak/standard_user.py). ACTION is set by the caller:
#   create        add "fmasoak" (non-admin) with a random password kept only on the host (root-only file)
#   autologin-on  sign it in at boot; saves the current auto-login (user + /etc/kcpassword) to restore later
#   autologin-off put the saved auto-login back
#   delete        autologin-off if needed, then remove the account and its home
# Nothing secret is printed: Fleet keeps script output in the host's activity.
ACTION="__ACTION__"
U=fmasoak; PW=/var/root/fma-soak-standard-user; BAK=/var/root/fma-soak-autologin-backup
is_admin() { dseditgroup -o checkmember -m "$U" admin >/dev/null 2>&1; }
restore() {  # put the saved auto-login back; prints who signs in at next boot
  prev=$(cat "$BAK/user")
  if [ -n "$prev" ]; then
    defaults write /Library/Preferences/com.apple.loginwindow autoLoginUser "$prev"
    [ -f "$BAK/kcpassword" ] && cp -p "$BAK/kcpassword" /etc/kcpassword
  else
    defaults delete /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null; rm -f /etc/kcpassword
  fi
  rm -rf "$BAK"; echo "autologin restored to ${prev:-none} (next boot)"
}
case $ACTION in
create)
  if id "$U" >/dev/null 2>&1; then echo "exists"; else
    umask 077; openssl rand -base64 18 | tr -d '\n' > "$PW"
    sysadminctl -addUser "$U" -fullName "FMA Soak Standard" -password "$(cat "$PW")" >/dev/null 2>&1
    id "$U" >/dev/null 2>&1 && echo "created" || { echo "create failed"; rm -f "$PW"; exit 1; }
  fi
  is_admin && { echo "ERROR: $U is an admin"; exit 1; }
  echo "standard user ok" ;;
autologin-on)
  id "$U" >/dev/null 2>&1 || { echo "no account: run create first"; exit 1; }
  if [ ! -d "$BAK" ]; then
    mkdir -m 700 "$BAK"
    defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser > "$BAK/user" 2>/dev/null || : > "$BAK/user"
    [ -f /etc/kcpassword ] && cp -p /etc/kcpassword "$BAK/kcpassword"
  fi
  sysadminctl -autologin set -userName "$U" -password "$(cat "$PW")" >/dev/null 2>&1
  [ "$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null)" = "$U" ] \
    && echo "autologin on (takes effect at next boot; was: $(cat "$BAK/user"))" || { echo "autologin set failed"; exit 1; } ;;
autologin-off)
  [ -d "$BAK" ] || { echo "no saved auto-login: nothing to restore"; exit 0; }
  restore ;;
delete)
  [ -d "$BAK" ] && restore
  [ "$(stat -f%Su /dev/console)" = "$U" ] && { echo "$U is signed in: sign it out first"; exit 1; }
  id "$U" >/dev/null 2>&1 && sysadminctl -deleteUser "$U" >/dev/null 2>&1
  rm -f "$PW"; id "$U" >/dev/null 2>&1 && echo "delete failed" || echo "deleted" ;;
*) echo "unknown action $ACTION"; exit 2 ;;
esac
