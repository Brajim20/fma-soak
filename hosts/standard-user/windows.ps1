# fma-soak phase 3: a standard (non-admin) local account, so the soak sees what customer fleets have at the console.
# Run as SYSTEM through Fleet (soak/standard_user.py). $Action is set by the caller:
#   create        add "fmasoak" (member of Users only) with a random password kept only on the host (SYSTEM/Administrators-only file)
#   autologin-on  sign it in at boot (Winlogon); saves the current auto-login values to restore later
#   autologin-off put the saved values back
#   delete        autologin-off if needed, then remove the account (its profile folder too)
# Nothing secret is printed: Fleet keeps script output in the host's activity.
$Action = '__ACTION__'
$U = 'fmasoak'; $Dir = 'C:\ProgramData\fma-soak'; $PwFile = "$Dir\standard-user.txt"; $Bak = "$Dir\autologin-backup.json"
$WL = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
function Lock($p) { icacls $p /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' | Out-Null }
function IsAdmin { [bool](Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*\$U" }) }
switch ($Action) {
  'create' {
    if (Get-LocalUser -Name $U -ErrorAction SilentlyContinue) { 'exists' } else {
      New-Item -ItemType Directory -Force -Path $Dir | Out-Null; Lock $Dir
      $b = New-Object byte[] 18; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
      $pw = [Convert]::ToBase64String($b); Set-Content -Path $PwFile -Value $pw -NoNewline; Lock $PwFile
      New-LocalUser -Name $U -FullName 'FMA Soak Standard' -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires | Out-Null
      Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $U -ErrorAction SilentlyContinue
      if (Get-LocalUser -Name $U -ErrorAction SilentlyContinue) { 'created' } else { 'create failed'; exit 1 }
    }
    if (IsAdmin) { "ERROR: $U is an admin"; exit 1 }
    # new local accounts get the privacy-settings screen at first sign-in; skip it so an auto-login reaches the desktop
    New-Item -Force 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\OOBE' | Out-Null
    New-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\OOBE' -Name DisablePrivacyExperience -Value 1 -PropertyType DWord -Force | Out-Null
    'standard user ok'
  }
  'autologin-on' {
    if (-not (Get-LocalUser -Name $U -ErrorAction SilentlyContinue)) { 'no account: run create first'; exit 1 }
    if (-not (Test-Path $Bak)) {
      $w = Get-ItemProperty -Path $WL
      @{ AutoAdminLogon = $w.AutoAdminLogon; DefaultUserName = $w.DefaultUserName; DefaultDomainName = $w.DefaultDomainName; DefaultPassword = $w.DefaultPassword } |
        ConvertTo-Json | Set-Content -Path $Bak; Lock $Bak
    }
    Set-ItemProperty -Path $WL -Name AutoAdminLogon -Value '1'
    Set-ItemProperty -Path $WL -Name DefaultUserName -Value $U
    Set-ItemProperty -Path $WL -Name DefaultDomainName -Value $env:COMPUTERNAME
    Set-ItemProperty -Path $WL -Name DefaultPassword -Value (Get-Content -Path $PwFile -Raw)
    $prev = (Get-Content $Bak -Raw | ConvertFrom-Json).DefaultUserName
    "autologin on (takes effect at next boot; was: $prev)"
  }
  'autologin-off' {
    if (-not (Test-Path $Bak)) { 'no saved auto-login: nothing to restore'; exit 0 }
    $b = Get-Content $Bak -Raw | ConvertFrom-Json
    foreach ($n in 'AutoAdminLogon', 'DefaultUserName', 'DefaultDomainName', 'DefaultPassword') {
      if ($null -ne $b.$n) { Set-ItemProperty -Path $WL -Name $n -Value $b.$n } else { Remove-ItemProperty -Path $WL -Name $n -ErrorAction SilentlyContinue }
    }
    Remove-Item -Path $Bak -Force
    "autologin restored to $($b.DefaultUserName) (next boot)"
  }
  'delete' {
    if (Test-Path $Bak) {
      $b = Get-Content $Bak -Raw | ConvertFrom-Json
      foreach ($n in 'AutoAdminLogon', 'DefaultUserName', 'DefaultDomainName', 'DefaultPassword') {
        if ($null -ne $b.$n) { Set-ItemProperty -Path $WL -Name $n -Value $b.$n } else { Remove-ItemProperty -Path $WL -Name $n -ErrorAction SilentlyContinue }
      }
      Remove-Item -Path $Bak -Force
    }
    $acct = Get-LocalUser -Name $U -ErrorAction SilentlyContinue
    if ($acct) {
      $prof = Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $acct.SID.Value }
      if ($prof -and $prof.Loaded) { "$U is signed in: sign it out first"; exit 1 }
      if ($prof) { $prof | Remove-CimInstance }
      Remove-LocalUser -Name $U
    }
    Remove-Item -Path $PwFile -Force -ErrorAction SilentlyContinue
    if (Get-LocalUser -Name $U -ErrorAction SilentlyContinue) { 'delete failed' } else { 'deleted' }
  }
  default { "unknown action $Action"; exit 2 }
}
