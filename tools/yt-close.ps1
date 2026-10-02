# Runs on bigpc as HASS.Agent command "yt_close" (packages/youtube_limit.yaml).
# Closes the active tab only when the foreground window is a browser showing
# YouTube, so a late or repeated press can never close something else.
# Arguments: the message to show, e.g. "YouTube: перерыв до 14:35".
# Saved as UTF-8 with BOM: Windows PowerShell 5.1 reads BOM-less files as ANSI.
Add-Type @'
using System; using System.Runtime.InteropServices; using System.Text;
public static class FgWin {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
'@
$h = [FgWin]::GetForegroundWindow()
$title = New-Object Text.StringBuilder 512
[void][FgWin]::GetWindowText($h, $title, 512)
$procId = [uint32]0
[void][FgWin]::GetWindowThreadProcessId($h, [ref]$procId)
$proc = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
if ($title.ToString() -notmatch 'YouTube' -or $proc -notin 'chrome', 'msedge', 'firefox', 'browser', 'opera') { exit }

$shell = New-Object -ComObject WScript.Shell
$shell.SendKeys('^w')
$msg = $args -join ' '
if (-not $msg) { $msg = 'YouTube: перерыв' }
[void]$shell.Popup($msg, 8, 'Лимит YouTube', 64)
