// Ultra Suite Sensor Helper: reads every sensor LibreHardwareMonitorLib finds, once a second, read-only.
// The library is only ever asked to Update() and report values; nothing here sets fan speeds, clocks or limits.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Threading;
using LibreHardwareMonitor.Hardware;

namespace UltraSuite.SensorHelper
{
    public sealed class HardwareReader : IDisposable
    {
        readonly HelperEdition _edition;
        readonly Action<string> _log;
        readonly object _gate = new object();
        Computer _computer;
        Thread _thread;
        volatile bool _stopping;
        volatile string _json;          // latest data.json document, null until the first read
        volatile int _sensorCount, _hardwareCount;
        long _updatedTicks;
        volatile DriverReport _driver = new DriverReport { Device = "error", DeviceError = -1 };
        volatile string _lastError;
        readonly DateTime _started = DateTime.UtcNow;

        public HardwareReader(HelperEdition edition, Action<string> log)
        {
            _edition = edition; _log = log;
        }

        public void Start()
        {
            _thread = new Thread(Loop) { IsBackground = true, Name = "sensor-reader" };
            _thread.Start();
        }

        public string Data() { return _json; }

        void Loop()
        {
            DateTime nextProbe = DateTime.MinValue;
            while (!_stopping)
            {
                try
                {
                    if (DateTime.UtcNow >= nextProbe)
                    {
                        _driver = DriverStatus.Probe();
                        nextProbe = DateTime.UtcNow.AddSeconds(30);
                    }
                    if (_computer == null) Open();
                    Read();
                    _lastError = null;
                }
                catch (Exception e)
                {
                    _lastError = e.GetType().Name + ": " + e.Message;
                    _log("read failed: " + _lastError);
                }
                for (int i = 0; i < 10 && !_stopping; i++) Thread.Sleep(100);
            }
        }

        void Open()
        {
            var c = new Computer
            {
                IsCpuEnabled = true, IsGpuEnabled = true, IsMemoryEnabled = true, IsMotherboardEnabled = true,
                IsControllerEnabled = true, IsNetworkEnabled = true, IsStorageEnabled = true, IsBatteryEnabled = true,
                IsPsuEnabled = true,
            };
            c.Open();
            lock (_gate) _computer = c;
            _log("opened LibreHardwareMonitorLib; PawnIO device " + _driver.Device);
        }

        void Read()
        {
            var devices = new List<Device>();
            int sensors = 0, hardware = 0;
            lock (_gate)
            {
                foreach (var h in _computer.Hardware)
                {
                    devices.Add(Convert(h, ref sensors, ref hardware));
                }
            }
            _json = TreeJson.Document(TreeBuilder.Build(Environment.MachineName, devices));
            _sensorCount = sensors;
            _hardwareCount = hardware;
            Interlocked.Exchange(ref _updatedTicks, DateTime.UtcNow.Ticks);
        }

        static Device Convert(IHardware h, ref int sensors, ref int hardware)
        {
            h.Update();
            hardware++;
            var d = new Device { Identifier = h.Identifier.ToString(), Name = h.Name, Kind = h.HardwareType.ToString() };
            foreach (var sub in h.SubHardware) d.SubDevices.Add(Convert(sub, ref sensors, ref hardware));
            foreach (var s in h.Sensors)
            {
                sensors++;
                d.Readings.Add(new Reading
                {
                    Identifier = s.Identifier.ToString(), Name = s.Name, Type = s.SensorType.ToString(),
                    Value = s.Value, Min = s.Min, Max = s.Max,
                });
            }
            return d;
        }

        public string Health()
        {
            long ticks = Interlocked.Read(ref _updatedTicks);
            string age = ticks == 0 ? "null"
                : ((long)(DateTime.UtcNow - new DateTime(ticks, DateTimeKind.Utc)).TotalMilliseconds).ToString(CultureInfo.InvariantCulture);
            return "{\"ok\":" + (_json != null ? "true" : "false")
                + ",\"helper\":\"UltraSuiteSensorHelper\""
                + ",\"version\":" + TreeJson.Quote(Program.Version)
                + ",\"edition\":" + TreeJson.Quote(_edition.Name)
                + ",\"port\":" + _edition.Port.ToString(CultureInfo.InvariantCulture)
                + ",\"read_only\":true"
                + ",\"elevated\":" + (Provisioner.IsElevated() ? "true" : "false")
                + ",\"sensor_count\":" + _sensorCount.ToString(CultureInfo.InvariantCulture)
                + ",\"hardware_count\":" + _hardwareCount.ToString(CultureInfo.InvariantCulture)
                + ",\"updated_ms_ago\":" + age
                + ",\"uptime_s\":" + ((long)(DateTime.UtcNow - _started).TotalSeconds).ToString(CultureInfo.InvariantCulture)
                + ",\"driver\":" + _driver.ToJson()
                + ",\"last_error\":" + (_lastError == null ? "null" : TreeJson.Quote(_lastError))
                + "}";
        }

        public void Dispose()
        {
            _stopping = true;
            if (_thread != null) _thread.Join(3000);
            lock (_gate)
            {
                if (_computer != null)
                {
                    try { _computer.Close(); } catch (Exception) { }
                    _computer = null;
                }
            }
        }
    }
}
