# @skill-name: media-controls
# @skill-description: Control Windows media playback and volume (playpause, next, prev, mute, volup, voldown)
# @skill-params: Action (playpause | next | prev | mute | volup | voldown)

param(
    [ValidateSet("playpause", "next", "prev", "mute", "volup", "voldown")]
    [string]$Action = "playpause"
)

$KeyMap = @{
    "playpause" = 0xB3 # VK_MEDIA_PLAY_PAUSE
    "next"      = 0xB0 # VK_MEDIA_NEXT_TRACK
    "prev"      = 0xB1 # VK_MEDIA_PREV_TRACK
    "mute"      = 0xAD # VK_VOLUME_MUTE
    "voldown"   = 0xAE # VK_VOLUME_DOWN
    "volup"     = 0xAF # VK_VOLUME_UP
}

$Vk = $KeyMap[$Action]

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class ZyloMediaKey {
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    public static void Tap(byte vk) {
        keybd_event(vk, 0, 0, UIntPtr.Zero);
        keybd_event(vk, 0, 2, UIntPtr.Zero);
    }
}
"@ -ErrorAction SilentlyContinue

[ZyloMediaKey]::Tap([byte]$Vk)
Write-Output "Sent Windows media key action: $Action"
