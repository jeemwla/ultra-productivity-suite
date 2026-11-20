// Ultra Suite Sensor Helper: edition identities.
// Each edition has its own service, folder and loopback port, so the Dev helper never stops, changes or answers for
// the Public one. pc_sentinel/backend/sensor_helper.py mirrors these values; tests/test_sensor_helper.py checks that
// both files agree and that neither port collides with a suite port or LibreHardwareMonitor's own 8085.
using System;

namespace UltraSuite.SensorHelper
{
    public sealed class HelperEdition
    {
        public string Name { get; private set; }
        public string ServiceName { get; private set; }
        public string DisplayName { get; private set; }
        public string InstallFolderName { get; private set; }
        public int Port { get; private set; }

        HelperEdition(string name, string service, string display, string folder, int port)
        {
            Name = name; ServiceName = service; DisplayName = display; InstallFolderName = folder; Port = port;
        }

        public static readonly HelperEdition Public = new HelperEdition(
            "Public", "UltraSuiteSensorHelper", "Ultra Suite Sensor Helper", "Ultra Suite Sensor Helper", 8095);

        public static readonly HelperEdition Dev = new HelperEdition(
            "Dev", "UltraSuiteSensorHelperDev", "Ultra Suite Sensor Helper (Dev)", "Ultra Suite Sensor Helper (Dev)", 18095);

        public static HelperEdition Parse(string name)
        {
            if (string.Equals(name, "Public", StringComparison.OrdinalIgnoreCase)) return Public;
            if (string.Equals(name, "Dev", StringComparison.OrdinalIgnoreCase)) return Dev;
            throw new ArgumentException("Unknown edition; expected Public or Dev");
        }

        public string InstallDirectory(string programFiles)
        {
            return System.IO.Path.Combine(programFiles, InstallFolderName);
        }

        public string Description
        {
            get
            {
                return "Reads temperatures, fan speeds and power for PC Sentinel and shares them only with programs on "
                    + "this PC (127.0.0.1:" + Port + "). Read-only: it never changes fans, clocks or other settings.";
            }
        }
    }
}
