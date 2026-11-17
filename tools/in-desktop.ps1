# Runs a program once in the logged-on user's desktop session (session 1) via a
# throwaway scheduled task. ssh lands in session 0, where windows are invisible.
param([string]$Exe, [string]$Arguments = '', [int]$WaitSec = 0)
$name = 'claude-oneshot'
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$act = if ($Arguments) { New-ScheduledTaskAction -Execute $Exe -Argument $Arguments } else { New-ScheduledTaskAction -Execute $Exe }
$ErrorActionPreference = 'Stop'
$pr = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
Register-ScheduledTask -TaskName $name -Action $act -Principal $pr | Out-Null
Start-ScheduledTask -TaskName $name
for ($i = 0; $i -lt $WaitSec; $i++) {
  Start-Sleep 1
  if ((Get-ScheduledTask -TaskName $name).State -ne 'Running') { break }
}
$info = Get-ScheduledTaskInfo -TaskName $name
'task state={0} result=0x{1:X}' -f (Get-ScheduledTask -TaskName $name).State, $info.LastTaskResult
Unregister-ScheduledTask -TaskName $name -Confirm:$false
