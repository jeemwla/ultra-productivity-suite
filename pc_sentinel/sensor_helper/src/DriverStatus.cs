// Ultra Suite Sensor Helper: is the PawnIO driver installed, and can this process open it?
// Read-only checks: the PawnIO uninstall entry, one CreateFile on its device, and the Memory Integrity flag that
// helps explain a blocked driver. Nothing here installs, loads or changes anything.
using System;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace UltraSuite.SensorHelper
{
    /// <summary>The official PawnIO installer this helper may download. Kept in step with pins.json (tests check).</summary>
    public static class PawnIoPin
    {
        public const string Version = "2.2.0";
        public const string Url = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe";
        public const string Sha256 = "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032";
        public const long Size = 3410960;
        // The only arguments ever passed. Never "-unrestricted" (that edition is unsigned and needs boot changes).
        public const string InstallArguments = "-install -silent";
        public const string UninstallKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO";
    }

    public sealed class DriverReport
    {
        public bool Installed;
        public string InstalledVersion;   // DisplayVersion of the PawnIO uninstall entry, e.g. "2.2.0.0"
        public string Device;             // "ok" | "not_installed" | "not_running" | "access_denied" | "error"
        public int DeviceError;           // Win32 error from opening the device, 0 when it opened
        public bool? MemoryIntegrity;     // Core isolation > Memory integrity, when Windows reports it

        public string ToJson()
        {
            return "{\"installed\":" + (Installed ? "true" : "false")
                + ",\"version\":" + (InstalledVersion == null ? "null" : TreeJson.Quote(InstalledVersion))
                + ",\"device\":" + TreeJson.Quote(Device ?? "error")
                + ",\"device_error\":" + DeviceError.ToString(CultureInfo.InvariantCulture)
                + ",\"memory_integrity\":" + (MemoryIntegrity.HasValue ? (MemoryIntegrity.Value ? "true" : "false") : "null")
                + "}";
        }
    }

    public static class DriverStatus
    {
        const string DevicePath = @"\\?\GLOBALROOT\Device\PawnIO";

        /// <summary>The installed PawnIO version from its uninstall entry (64-bit view first), or null.</summary>
        public static Version InstalledVersion(out string text)
        {
            text = null;
            foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                try
                {
                    using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view))
                    using (var key = hklm.OpenSubKey(PawnIoPin.UninstallKey))
                    {
                        var s = key == null ? null : key.GetValue("DisplayVersion") as string;
                        Version v;
                        if (s != null && Version.TryParse(s.Trim(), out v)) { text = s.Trim(); return v; }
                    }
                }
                catch (Exception) { }
            }
            return null;
        }

        /// <summary>True when the installed version is the pinned one or newer, so no install is needed.</summary>
        public static bool SatisfiesPin(Version installed)
        {
            return installed != null && Normalize(installed) >= Normalize(new Version(PawnIoPin.Version));
        }

        public static Version Normalize(Version v)
        {
            return new Version(v.Major, Math.Max(v.Minor, 0), Math.Max(v.Build, 0), Math.Max(v.Revision, 0));
        }

        public static string Classify(bool installed, int error)
        {
            if (error == 0) return "ok";
            if (error == 5) return "access_denied";
            // 2 / 3: no such device. Installed but absent usually means Windows blocked the driver or it did not start.
            if (error == 2 || error == 3) return installed ? "not_running" : "not_installed";
            return "error";
        }

        public static DriverReport Probe()
        {
            string text;
            var v = InstalledVersion(out text);
            var r = new DriverReport { Installed = v != null, InstalledVersion = text };
            int error = OpenDevice();
            r.DeviceError = error;
            r.Device = Classify(r.Installed, error);
            r.MemoryIntegrity = ReadMemoryIntegrity();
            return r;
        }

        static int OpenDevice()
        {
            IntPtr h = CreateFile(DevicePath, GenericRead | GenericWrite, FileShareRead | FileShareWrite, IntPtr.Zero,
                OpenExisting, 0, IntPtr.Zero);
            if (h == InvalidHandle) return Marshal.GetLastWin32Error();
            CloseHandle(h);
            return 0;
        }

        static bool? ReadMemoryIntegrity()
        {
            try
            {
                using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                using (var key = hklm.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"))
                {
                    object value = key == null ? null : key.GetValue("Enabled");
                    if (value is int) return (int)value != 0;
                }
            }
            catch (Exception) { }
            return null;
        }

        public static string Describe(int error)
        {
            return new Win32Exception(error).Message;
        }

        const uint GenericRead = 0x80000000, GenericWrite = 0x40000000;
        const uint FileShareRead = 1, FileShareWrite = 2, OpenExisting = 3;
        static readonly IntPtr InvalidHandle = new IntPtr(-1);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);
    }
}
