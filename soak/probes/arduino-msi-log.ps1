# Arduino IDE 2.3.10 (x64 MSI) on Windows ARM: run the FMA's msiexec line with a full verbose log and print only the
# failure context (Fleet keeps just the last 500 lines of the log, which missed the cause of the 1603)
$ProgressPreference = "SilentlyContinue"
$dir = "C:\ProgramData\fma-probe"; New-Item -ItemType Directory -Force $dir | Out-Null
$msi = "$dir\arduino.msi"; $log = "$dir\arduino.log"
Invoke-WebRequest "https://github.com/arduino/arduino-ide/releases/download/2.3.10/arduino-ide_2.3.10_Windows_64bit.msi" -OutFile $msi -UseBasicParsing
"downloaded {0:N0} bytes" -f (Get-Item $msi).Length
$p = Start-Process msiexec.exe -ArgumentList "/quiet /norestart /l*v `"$log`" /i `"$msi`"" -PassThru -Wait
"msiexec exit: $($p.ExitCode)"
$lines = Get-Content $log
$i = ($lines | Select-String -Pattern "Return value 3" | Select-Object -First 1).LineNumber
if ($i) { "--- first 'Return value 3' at line $i"; $lines[[math]::Max(0,$i-40)..($i+2)] }
"--- errors"; $lines | Select-String -Pattern "Error \d{4}|error code|CustomAction .* returned|MainEngineThread is returning" | Select-Object -First 15 | ForEach-Object { $_.Line }
$app = Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "Arduino IDE*" }
if ($app) {
  "--- installed: $($app.DisplayName) $($app.DisplayVersion); removing"
  Start-Process msiexec.exe -ArgumentList "/x $($app.PSChildName) /quiet /norestart" -Wait
}
Remove-Item $msi -Force
