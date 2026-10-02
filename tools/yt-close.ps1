# Body of the HASS.Agent PowerShell command "yt_close" on bigpc
# (packages/youtube_limit.yaml); pasted inline into commands.json, not run as a
# file: bigpc's execution policy is AllSigned, and HASS.Agent runs inline
# commands via -EncodedCommand, which the policy does not cover. Inline
# commands get no action argument, hence the fixed message.
# Closes the active tab only when the foreground window is a browser showing
# YouTube, so a late or repeated press can never close something else.
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
[void]$shell.Popup('Сейчас перерыв или ночь. Выключатель: «Пульт» → «YouTube на компе».', 8, 'Лимит YouTube', 64)
