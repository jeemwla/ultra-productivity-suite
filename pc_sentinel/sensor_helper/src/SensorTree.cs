// Ultra Suite Sensor Helper: the sensor tree and its JSON.
// The JSON matches LibreHardwareMonitor's own web server (data.json): a root "Sensor" node, one computer node, then
// hardware nodes (with HardwareId), optional sub-hardware nodes, sensor-type nodes and sensor nodes (with SensorId and
// Type). Values are text with units ("45.0 °C"), always written with a dot decimal separator. PC Sentinel reads either
// source with the same parser. This file has no LibreHardwareMonitorLib dependency so it can be tested on its own.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace UltraSuite.SensorHelper
{
    public sealed class TreeNode
    {
        public string Text = "";
        public string Min = "";
        public string Value = "";
        public string Max = "";
        public string ImageUrl = "";
        public string SensorId;      // sensor nodes only
        public string Type;          // sensor nodes only
        public string HardwareId;    // hardware nodes only
        public string RawMin, RawValue, RawMax; // sensor nodes only, unscaled
        public readonly List<TreeNode> Children = new List<TreeNode>();

        public TreeNode Add(TreeNode child) { Children.Add(child); return child; }
    }

    public static class SensorFormat
    {
        // Same formats as LibreHardwareMonitor's SensorNode (v0.9.6), with the invariant culture.
        static readonly Dictionary<string, string> Formats = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            { "Voltage", "{0:F3} V" }, { "Current", "{0:F3} A" }, { "Clock", "{0:F1} MHz" }, { "Load", "{0:F1} %" },
            { "Temperature", "{0:F1} °C" }, { "Fan", "{0:F0} RPM" }, { "Flow", "{0:F1} L/h" },
            { "Control", "{0:F1} %" }, { "Level", "{0:F1} %" }, { "Power", "{0:F1} W" }, { "Data", "{0:F1} GB" },
            { "SmallData", "{0:F1} MB" }, { "Factor", "{0:F3}" }, { "Frequency", "{0:F1} Hz" },
            { "Throughput", "{0:F1} B/s" }, { "Timing", "{0:F3} ns" }, { "Energy", "{0:F0} mWh" },
            { "Noise", "{0:F0} dBA" }, { "Conductivity", "{0:F1} µS/cm" }, { "Humidity", "{0:F0} %" },
        };

        // Type-group captions, in LibreHardwareMonitor's display order.
        public static readonly string[] TypeOrder =
        {
            "Voltage", "Current", "Power", "Clock", "Temperature", "Load", "Frequency", "Fan", "Flow", "Control",
            "Level", "Factor", "Data", "SmallData", "Throughput", "TimeSpan", "Timing", "Energy", "Noise",
            "Conductivity", "Humidity",
        };

        static readonly Dictionary<string, string> Captions = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            { "Voltage", "Voltages" }, { "Current", "Currents" }, { "Power", "Powers" }, { "Clock", "Clocks" },
            { "Temperature", "Temperatures" }, { "Load", "Load" }, { "Frequency", "Frequencies" }, { "Fan", "Fans" },
            { "Flow", "Flows" }, { "Control", "Controls" }, { "Level", "Levels" }, { "Factor", "Factors" },
            { "Data", "Data" }, { "SmallData", "Data" }, { "Throughput", "Throughput" }, { "TimeSpan", "Times" },
            { "Timing", "Timings" }, { "Energy", "Energy" }, { "Noise", "Noise" }, { "Conductivity", "Conductivities" },
            { "Humidity", "Humidity" },
        };

        public static string Caption(string type)
        {
            string caption;
            return type != null && Captions.TryGetValue(type, out caption) ? caption : (type ?? "Other");
        }

        public static int Order(string type)
        {
            int i = Array.IndexOf(TypeOrder, type);
            return i < 0 ? TypeOrder.Length : i;
        }

        /// <summary>Text for one reading, or "-" when the sensor has no value (as LibreHardwareMonitor shows it).</summary>
        public static string Format(string type, float? value)
        {
            if (!value.HasValue || float.IsNaN(value.Value) || float.IsInfinity(value.Value)) return "-";
            if (type == "TimeSpan")
                return TimeSpan.FromSeconds(value.Value).ToString("g", CultureInfo.InvariantCulture);
            string format;
            if (type == null || !Formats.TryGetValue(type, out format)) format = "{0:F3}";
            return string.Format(CultureInfo.InvariantCulture, format, value.Value);
        }
    }

    public static class TreeJson
    {
        /// <summary>The whole document: LibreHardwareMonitor's root node with sequential ids.</summary>
        public static string Document(TreeNode computer)
        {
            var root = new TreeNode { Text = "Sensor", Min = "Min", Value = "Value", Max = "Max", ImageUrl = "" };
            root.Children.Add(computer);
            var sb = new StringBuilder(64 * 1024);
            int id = 0;
            Write(sb, root, ref id);
            return sb.ToString();
        }

        static void Write(StringBuilder sb, TreeNode n, ref int id)
        {
            sb.Append("{\"id\":").Append(id++.ToString(CultureInfo.InvariantCulture));
            Field(sb, "Text", n.Text);
            Field(sb, "Min", n.Min);
            Field(sb, "Value", n.Value);
            Field(sb, "Max", n.Max);
            if (n.SensorId != null)
            {
                Field(sb, "SensorId", n.SensorId);
                Field(sb, "Type", n.Type ?? "");
                Field(sb, "RawMin", n.RawMin ?? "");
                Field(sb, "RawValue", n.RawValue ?? "");
                Field(sb, "RawMax", n.RawMax ?? "");
            }
            if (n.HardwareId != null) Field(sb, "HardwareId", n.HardwareId);
            Field(sb, "ImageURL", n.ImageUrl ?? "");
            sb.Append(",\"Children\":[");
            for (int i = 0; i < n.Children.Count; i++)
            {
                if (i > 0) sb.Append(',');
                Write(sb, n.Children[i], ref id);
            }
            sb.Append("]}");
        }

        static void Field(StringBuilder sb, string name, string value)
        {
            sb.Append(",\"").Append(name).Append("\":");
            Quote(sb, value);
        }

        public static void Quote(StringBuilder sb, string value)
        {
            sb.Append('"');
            foreach (char c in value ?? "")
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        // Control characters and lone surrogates are escaped; the document stays plain ASCII-safe JSON.
                        if (c < 0x20 || c == 0x2028 || c == 0x2029 || char.IsSurrogate(c))
                            sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }

        public static string Quote(string value)
        {
            var sb = new StringBuilder();
            Quote(sb, value);
            return sb.ToString();
        }
    }

    /// <summary>A reading as LibreHardwareMonitorLib reports it, without depending on that library's types.</summary>
    public sealed class Reading
    {
        public string Identifier, Name, Type;
        public float? Value, Min, Max;
    }

    /// <summary>A hardware item with its readings and sub-hardware.</summary>
    public sealed class Device
    {
        public string Identifier, Name, Kind;
        public readonly List<Reading> Readings = new List<Reading>();
        public readonly List<Device> SubDevices = new List<Device>();
    }

    public static class TreeBuilder
    {
        /// <summary>Computer > hardware > (sub-hardware) > sensor-type groups > sensors, as LibreHardwareMonitor nests them.</summary>
        public static TreeNode Build(string machineName, IEnumerable<Device> devices)
        {
            var computer = new TreeNode { Text = machineName ?? "Computer", ImageUrl = "images_icon/computer.png" };
            foreach (var d in devices) computer.Children.Add(DeviceNode(d));
            return computer;
        }

        static TreeNode DeviceNode(Device d)
        {
            var node = new TreeNode
            {
                Text = d.Name ?? "Unknown", HardwareId = d.Identifier ?? "", ImageUrl = "images_icon/" + Icon(d.Kind),
            };
            foreach (var sub in d.SubDevices) node.Children.Add(DeviceNode(sub));
            // One group per sensor type: known types in LibreHardwareMonitor's order, unknown ones after them by name.
            var byType = new Dictionary<string, List<Reading>>(StringComparer.Ordinal);
            foreach (var r in d.Readings)
            {
                string type = r.Type ?? "";
                List<Reading> list;
                if (!byType.TryGetValue(type, out list)) byType[type] = list = new List<Reading>();
                list.Add(r);
            }
            var groups = new List<KeyValuePair<string, List<Reading>>>(byType);
            groups.Sort((a, b) =>
            {
                int c = SensorFormat.Order(a.Key).CompareTo(SensorFormat.Order(b.Key));
                return c != 0 ? c : string.CompareOrdinal(a.Key, b.Key);
            });
            foreach (var g in groups)
            {
                var typeNode = node.Add(new TreeNode
                {
                    Text = SensorFormat.Caption(g.Key), ImageUrl = "images_icon/" + (g.Key ?? "other").ToLowerInvariant() + ".png",
                });
                foreach (var r in g.Value)
                {
                    typeNode.Children.Add(new TreeNode
                    {
                        Text = r.Name ?? "", SensorId = r.Identifier ?? "", Type = r.Type ?? "",
                        Min = SensorFormat.Format(r.Type, r.Min), Value = SensorFormat.Format(r.Type, r.Value),
                        Max = SensorFormat.Format(r.Type, r.Max),
                        RawMin = Raw(r.Min), RawValue = Raw(r.Value), RawMax = Raw(r.Max),
                        ImageUrl = "images/transparent.png",
                    });
                }
            }
            return node;
        }

        static string Raw(float? v)
        {
            if (!v.HasValue || float.IsNaN(v.Value) || float.IsInfinity(v.Value)) return "-";
            return v.Value.ToString("R", CultureInfo.InvariantCulture);
        }

        static string Icon(string kind)
        {
            switch ((kind ?? "").ToLowerInvariant())
            {
                case "cpu": return "cpu.png";
                case "gpuamd": case "gpunvidia": case "gpuintel": return "gpu.png";
                case "memory": return "ram.png";
                case "storage": return "hdd.png";
                case "motherboard": case "superio": case "embeddedcontroller": return "mainboard.png";
                case "network": return "nic.png";
                case "battery": return "battery.png";
                case "psu": return "power-supply.png";
                default: return "chip.png";
            }
        }
    }
}
