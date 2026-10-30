# VSCodium 1.143.17232 (x64 Inno Setup) on Windows ARM: the FMA's install line plus /LOG, printing the setup log's
# errors and tail (the FMA script only reports the exit code)
$ProgressPreference = "SilentlyContinue"
$dir = "C:\ProgramData\fma-probe"; New-Item -ItemType Directory -Force $dir | Out-Null
$exe = "$dir\vscodium.exe"; $log = "$dir\vscodium.log"
Invoke-WebRequest "https://github.com/VSCodium/vscodium/releases/download/1.143.17232/VSCodiumSetup-x64-1.143.17232.exe" -OutFile $exe -UseBasicParsing
"downloaded {0:N0} bytes" -f (Get-Item $exe).Length
$p = Start-Process $exe -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/mergetasks=!runcode","/LOG=`"$log`"" -PassThru -Wait
"setup exit: $($p.ExitCode)"
"--- errors"; Get-Content $log | Select-String -Pattern "error|fail|abort|denied|exit code" | Select-Object -First 20 | ForEach-Object { $_.Line }
"--- tail"; Get-Content $log -Tail 25
$app = Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "VSCodium*" }
if ($app) {
  "--- installed: $($app.DisplayName) $($app.DisplayVersion); removing"
  Start-Process ($app.UninstallString -replace '"','') -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES" -Wait
}
Remove-Item $exe -Force
