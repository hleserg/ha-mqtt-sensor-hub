# Body of the HASS.Agent PowerShell command "bedtime" on bigpc: both buttons of
# the bed switch (packages/bed_switch.yaml). Pasted inline into commands.json,
# like tools/yt-close.ps1 and for the same reason (AllSigned). Plain ASCII.
# Closes Minecraft, then every YouTube tab in Chrome. HA turns the monitor off
# after it: Ctrl+W wakes the monitor. Last run is logged to %TEMP%\bedtime.log.
$DryRun = $false
$sw = [Diagnostics.Stopwatch]::StartNew()
$log = @((Get-Date -Format s) + ' dry=' + $DryRun)

# Minecraft Java under any launcher: javaw with "minecraft" in its command line
# (Prism sends the game arguments on stdin, so there is no --gameDir to match).
# Closing the window makes the game save the world and quit. Never killed: on
# this modpack the save takes ~18 s, and a kill mid-save corrupts the world.
$mc = @(Get-CimInstance Win32_Process -Filter "Name='javaw.exe' OR Name='java.exe'" |
  Where-Object { $_.CommandLine -match 'minecraft' } |
  ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } |
  Where-Object { $_.MainWindowHandle -ne 0 })
$log += 'minecraft windows: ' + $mc.Count
if (-not $DryRun) {
  foreach ($p in $mc) { [void]$p.CloseMainWindow() }
  # ponytail: waits at most 4 s; a fullscreen game still closing may keep Chrome
  # from coming forward, then YouTube survives. Upgrade: a second press later.
  while ($mc.Count -and $sw.ElapsedMilliseconds -lt 4000) {
    Start-Sleep -Milliseconds 250
    $mc = @($mc | Where-Object { $_.Refresh(); -not $_.HasExited -and $_.MainWindowHandle -ne 0 })
  }
  $log += 'minecraft still open: ' + $mc.Count + ' t=' + $sw.ElapsedMilliseconds
}

# YouTube tabs: Chrome hides the tab close button from UI Automation, but a tab
# can be selected. Select it, bring its window forward, and send Ctrl+W only
# after checking that the focused window is that one and shows YouTube.
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class FgWin { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
'@
$A = [Windows.Automation.AutomationElement]
$TS = [Windows.Automation.TreeScope]
$tabCond = New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::TabItem)
$shell = New-Object -ComObject WScript.Shell
$closed = 0
for ($i = 0; $i -lt 10; $i++) {
  $ids = @(Get-Process chrome -ErrorAction SilentlyContinue | ForEach-Object Id)
  $tab = $null
  foreach ($w in $A::RootElement.FindAll($TS::Children, [Windows.Automation.Condition]::TrueCondition)) {
    if ($ids -notcontains $w.Current.ProcessId) { continue }
    $yt = @($w.FindAll($TS::Descendants, $tabCond) | Where-Object { $_.Current.Name -match 'YouTube' })
    if ($DryRun) { $log += 'chrome window: youtube tabs ' + $yt.Count; continue }
    if ($yt.Count) { $tab = $yt[0]; break }
  }
  if (-not $tab) { break }
  $tab.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
  $wp = $w.GetCurrentPattern([Windows.Automation.WindowPattern]::Pattern)
  if ($wp.Current.WindowVisualState -eq 'Minimized') { $wp.SetWindowVisualState('Normal') }
  $w.SetFocus()
  Start-Sleep -Milliseconds 300
  # ponytail: a background process may be refused the foreground (Windows
  # foreground lock); then this check stops it. Upgrade: Alt-key trick.
  if ([FgWin]::GetForegroundWindow().ToInt64() -ne $w.Current.NativeWindowHandle -or $w.Current.Name -notmatch 'YouTube') {
    $log += 'chrome not in front, stopped'
    break
  }
  $shell.SendKeys('^w')
  $closed++
  Start-Sleep -Milliseconds 400
}
$log += 'youtube tabs closed: ' + $closed + ' t=' + $sw.ElapsedMilliseconds
$log | Set-Content "$env:TEMP\bedtime.log"
