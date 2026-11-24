# @skill-name: media-controls
# @skill-description: Control playback or set the default Windows output volume to an exact percentage.
# @skill-params: Action (playpause | next | prev | mute | volup | voldown | setvolume); Level (integer 0-100, required for setvolume; e.g. Action=setvolume, Level=70)

param(
    [ValidateSet("playpause", "next", "prev", "mute", "volup", "voldown", "setvolume")]
    [string]$Action = "playpause",
    [ValidateRange(0, 100)]
    [int]$Level
)

if ($Action -eq "setvolume") {
    if (-not $PSBoundParameters.ContainsKey("Level")) {
        throw "Provide -Level from 0 to 100."
    }
    Add-Type -ErrorAction Stop -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class ZyloAudioEndpoint {
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    private class DeviceEnumerator {}

    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IDeviceEnumerator {
        void EnumAudioEndpoints(int flow, uint flags, out IntPtr devices);
        void GetDefaultAudioEndpoint(int flow, int role, out IDevice device);
    }

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IDevice {
        void Activate(ref Guid iid, uint context, IntPtr parameters,
            [MarshalAs(UnmanagedType.IUnknown)] out object device);
    }

    [ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IEndpointVolume {
        void RegisterControlChangeNotify(IntPtr callback);
        void UnregisterControlChangeNotify(IntPtr callback);
        void GetChannelCount(out uint count);
        void SetMasterVolumeLevel(float decibels, IntPtr context);
        void SetMasterVolumeLevelScalar(float level, IntPtr context);
        void GetMasterVolumeLevel(out float decibels);
        void GetMasterVolumeLevelScalar(out float level);
    }

    public static int SetVolume(int level) {
        var enumerator = (IDeviceEnumerator)new DeviceEnumerator();
        IDevice device = null;
        object endpoint = null;
        try {
            enumerator.GetDefaultAudioEndpoint(0, 1, out device); // eRender, eMultimedia
            Guid volumeId = typeof(IEndpointVolume).GUID;
            device.Activate(ref volumeId, 23, IntPtr.Zero, out endpoint); // CLSCTX_ALL
            var volume = (IEndpointVolume)endpoint;
            volume.SetMasterVolumeLevelScalar(level / 100f, IntPtr.Zero);
            float applied;
            volume.GetMasterVolumeLevelScalar(out applied);
            return (int)Math.Round(applied * 100);
        } finally {
            if (endpoint != null) Marshal.ReleaseComObject(endpoint);
            if (device != null) Marshal.ReleaseComObject(device);
            Marshal.ReleaseComObject(enumerator);
        }
    }
}
"@
    $applied = [ZyloAudioEndpoint]::SetVolume($Level)
    Write-Output "Volume set to ${applied}%."
    return
}

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
