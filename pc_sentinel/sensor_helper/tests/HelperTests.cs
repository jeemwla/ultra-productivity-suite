// Tests for the parts of the sensor helper that do not need LibreHardwareMonitorLib, the PawnIO driver or
// administrator rights: JSON shape, value formats, request handling, the loopback socket, argument parsing, the
// service command line and the setup decisions. Built and run by tools/build_sensor_helper.ps1 -TestOnly.
// Nothing here installs, downloads or changes services.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UltraSuite.SensorHelper;

static class HelperTests
{
    static int _passed, _failed;

    static void Check(bool ok, string name)
    {
        if (ok) _passed++;
        else { _failed++; Console.WriteLine("FAIL " + name); }
    }

    static void Throws<T>(Action a, string name) where T : Exception
    {
        try { a(); Check(false, name + " (no exception)"); }
        catch (T) { Check(true, name); }
    }

    static int Main()
    {
        var tests = new Action[]
        {
            JsonShape, FormatsAreInvariant, MissingValues, Escaping, ParseRequests, Routing, HeadersAreBounded,
            Socket, OptionsParsing, Quoting, ServiceArguments, ResultPaths, PawnIoDecisions, DriverClassification,
            Hashing, ResultIni, EditionsAreIsolated,
        };
        foreach (var t in tests)
        {
            try { t(); }
            catch (Exception e) { _failed++; Console.WriteLine("FAIL " + t.Method.Name + ": " + e); }
        }
        Console.WriteLine("sensor helper tests: " + _passed + " passed, " + _failed + " failed");
        return _failed == 0 ? 0 : 1;
    }

    static TreeNode Sample()
    {
        var cpu = new Device { Identifier = "/amdcpu/0", Name = "AMD Ryzen 7 7800X3D", Kind = "Cpu" };
        cpu.Readings.Add(new Reading { Identifier = "/amdcpu/0/temperature/2", Name = "Core (Tctl/Tdie)", Type = "Temperature", Value = 61.25f, Min = 40f, Max = 80.5f });
        cpu.Readings.Add(new Reading { Identifier = "/amdcpu/0/load/0", Name = "CPU Total", Type = "Load", Value = 12.34f, Min = 0f, Max = 100f });
        cpu.Readings.Add(new Reading { Identifier = "/amdcpu/0/clock/1", Name = "Core #1", Type = "Clock", Value = 4950.2f });
        var board = new Device { Identifier = "/motherboard", Name = "B650", Kind = "Motherboard" };
        var lpc = new Device { Identifier = "/lpc/nct6799d/0", Name = "Nuvoton NCT6799D", Kind = "SuperIO" };
        lpc.Readings.Add(new Reading { Identifier = "/lpc/nct6799d/0/fan/1", Name = "Fan #2", Type = "Fan", Value = 1033f });
        board.SubDevices.Add(lpc);
        return TreeBuilder.Build("TESTPC", new[] { cpu, board });
    }

    static void JsonShape()
    {
        string json = TreeJson.Document(Sample());
        Check(json.StartsWith("{\"id\":0,\"Text\":\"Sensor\",\"Min\":\"Min\",\"Value\":\"Value\",\"Max\":\"Max\",\"ImageURL\":\"\",\"Children\":[{\"id\":1,\"Text\":\"TESTPC\"", StringComparison.Ordinal), "root and computer nodes");
        Check(json.Contains("\"HardwareId\":\"/amdcpu/0\""), "hardware id");
        Check(json.Contains("\"SensorId\":\"/amdcpu/0/temperature/2\",\"Type\":\"Temperature\""), "sensor id and type");
        Check(json.Contains("\"Value\":\"61.3 \u00B0C\"") || json.Contains("\"Value\":\"61.2 \u00B0C\""), "temperature text");
        Check(json.Contains("\"Text\":\"Temperatures\""), "type group caption");
        Check(json.Contains("\"HardwareId\":\"/lpc/nct6799d/0\""), "sub-hardware nested");
        Check(json.IndexOf("\"Text\":\"Clocks\"", StringComparison.Ordinal) < json.IndexOf("\"Text\":\"Temperatures\"", StringComparison.Ordinal), "LibreHardwareMonitor type order");
        // ids are sequential and unique
        var ids = new List<int>();
        int at = 0;
        while ((at = json.IndexOf("{\"id\":", at, StringComparison.Ordinal)) >= 0)
        {
            at += 6;
            int end = json.IndexOf(',', at);
            ids.Add(int.Parse(json.Substring(at, end - at), CultureInfo.InvariantCulture));
        }
        bool sequential = ids.Count > 5;
        for (int i = 0; i < ids.Count; i++) sequential &= ids[i] == i;
        Check(sequential, "sequential ids");
    }

    static void FormatsAreInvariant()
    {
        var before = Thread.CurrentThread.CurrentCulture;
        try
        {
            Thread.CurrentThread.CurrentCulture = new CultureInfo("de-DE");
            Check(SensorFormat.Format("Voltage", 1.2345f) == "1.235 V" || SensorFormat.Format("Voltage", 1.2345f) == "1.234 V", "voltage dot decimal");
            Check(SensorFormat.Format("Fan", 1033.4f) == "1033 RPM", "fan");
            Check(SensorFormat.Format("Power", 65.04f) == "65.0 W", "power");
            Check(SensorFormat.Format("Throughput", 1500f) == "1500.0 B/s", "throughput in bytes");
            Check(SensorFormat.Format("SomethingNew", 2f) == "2.000", "unknown type");
        }
        finally { Thread.CurrentThread.CurrentCulture = before; }
    }

    static void MissingValues()
    {
        Check(SensorFormat.Format("Temperature", null) == "-", "null value");
        Check(SensorFormat.Format("Temperature", float.NaN) == "-", "NaN value");
        Check(SensorFormat.Format("Temperature", float.PositiveInfinity) == "-", "infinite value");
    }

    static void Escaping()
    {
        Check(TreeJson.Quote("a\"b\\c\n\u0001") == "\"a\\\"b\\\\c\\n\\u0001\"", "escapes");
        Check(TreeJson.Quote(null) == "\"\"", "null string");
    }

    static void ParseRequests()
    {
        var ok = RequestHead.Parse("GET /data.json HTTP/1.1\r\nHost: 127.0.0.1:8095\r\nAccept: */*");
        Check(ok.Valid && ok.Method == "GET" && ok.Path == "/data.json" && ok.Host == "127.0.0.1:8095", "valid request");
        Check(RequestHead.Parse("GET /data.json?x=1 HTTP/1.1\r\nHost: localhost").Path == "/data.json", "query string ignored");
        Check(!RequestHead.Parse("GET /data.json HTTP/1.1").Valid, "HTTP/1.1 needs Host");
        Check(RequestHead.Parse("GET /data.json HTTP/1.0").Valid, "HTTP/1.0 without Host");
        Check(!RequestHead.Parse("GET /data.json HTTP/1.1\r\nHost: a\r\nHost: b").Valid, "duplicate Host refused");
        Check(!RequestHead.Parse("GET  /data.json HTTP/1.1\r\nHost: a").Valid, "malformed request line");
        Check(!RequestHead.Parse("GET http://x/data.json HTTP/1.1\r\nHost: a").Valid, "absolute form refused");
        Check(RequestHead.Parse("GET / HTTP/2.0\r\nHost: a").Error == 505, "unknown version");
        Check(!RequestHead.Parse("GET / HTTP/1.1\r\nHost : a").Valid, "space before colon refused");
        Check(RequestHead.Parse(new string('a', RequestHead.MaxHeaderBytes + 1)).Error == 431, "oversized head");
        Check(RequestHead.LoopbackHost("LOCALHOST:8095", 8095) && RequestHead.LoopbackHost("[::1]", 8095), "loopback names");
        Check(!RequestHead.LoopbackHost("evil.example:8095", 8095) && !RequestHead.LoopbackHost("127.0.0.1:9999", 8095), "foreign hosts");
    }

    static Router TestRouter(int port, string data)
    {
        return new Router(port, () => data, () => "{\"ok\":true}");
    }

    static void Routing()
    {
        var router = TestRouter(8095, "{\"id\":0}");
        Func<string, Response> get = head => router.Handle(RequestHead.Parse(head));
        Check(get("GET /data.json HTTP/1.1\r\nHost: 127.0.0.1:8095").Status == 200, "data 200");
        Check(get("HEAD /health HTTP/1.1\r\nHost: 127.0.0.1:8095").Status == 200, "health HEAD 200");
        var post = get("POST /data.json HTTP/1.1\r\nHost: 127.0.0.1:8095");
        Check(post.Status == 405 && post.Allow == "GET, HEAD", "POST refused, read-only");
        Check(get("PUT /health HTTP/1.1\r\nHost: 127.0.0.1:8095").Status == 405, "PUT refused");
        Check(get("GET /Sensor HTTP/1.1\r\nHost: 127.0.0.1:8095").Status == 404, "LHM write path absent");
        Check(get("POST /Sensor HTTP/1.1\r\nHost: 127.0.0.1:8095").Status == 404, "LHM set path absent");
        Check(get("GET / HTTP/1.1\r\nHost: 127.0.0.1:8095").Status == 404, "no index page");
        Check(get("GET /data.json HTTP/1.1\r\nHost: attacker.example").Status == 403, "rebinding host refused");
        Check(TestRouter(8095, null).Handle(RequestHead.Parse("GET /data.json HTTP/1.1\r\nHost: localhost")).Status == 503, "starting");
        var bytes = Encoding.ASCII.GetString(get("GET /data.json HTTP/1.1\r\nHost: localhost:8095").Serialize(true));
        Check(bytes.Contains("Cache-Control: no-store") && !bytes.Contains("Access-Control"), "no caching, no CORS");
        var head = Encoding.ASCII.GetString(get("HEAD /data.json HTTP/1.1\r\nHost: localhost:8095").Serialize(false));
        Check(head.EndsWith("\r\n\r\n", StringComparison.Ordinal), "HEAD has no body");
    }

    static void HeadersAreBounded()
    {
        var big = new MemoryStream(Encoding.ASCII.GetBytes("GET / HTTP/1.1\r\nX: " + new string('a', 9000) + "\r\n\r\n"));
        Check(LoopbackServer.ReadHead(big) == null, "oversized headers rejected");
        var small = new MemoryStream(Encoding.ASCII.GetBytes("GET /health HTTP/1.1\r\nHost: localhost\r\n\r\nBODY"));
        Check(LoopbackServer.ReadHead(small) == "GET /health HTTP/1.1\r\nHost: localhost", "head stops at blank line");
    }

    static string Send(int port, string request)
    {
        using (var c = new TcpClient())
        {
            c.Connect(IPAddress.Loopback, port);
            var s = c.GetStream();
            var b = Encoding.ASCII.GetBytes(request);
            s.Write(b, 0, b.Length);
            using (var r = new StreamReader(s, Encoding.UTF8)) return r.ReadToEnd();
        }
    }

    static void Socket()
    {
        // Port 0 picks a free port so the test never touches the real helper ports.
        int port = 0;
        Router router = null;
        var server = new LoopbackServer(0, new Router(0, () => "{\"x\":1}", () => "{\"ok\":true}"));
        server.Start();
        try
        {
            var ep = server.LocalEndpoint;
            Check(IPAddress.IsLoopback(ep.Address) && ep.Address.Equals(IPAddress.Loopback), "bound to 127.0.0.1 only");
            port = ep.Port;
        }
        finally { server.Dispose(); }
        router = new Router(port, () => "{\"x\":1}", () => "{\"ok\":true}");
        server = new LoopbackServer(port, router);
        server.Start();
        try
        {
            string host = "Host: 127.0.0.1:" + port + "\r\n";
            Check(Send(port, "GET /data.json HTTP/1.1\r\n" + host + "\r\n").Contains("{\"x\":1}"), "serves data");
            Check(Send(port, "DELETE /data.json HTTP/1.1\r\n" + host + "\r\n").StartsWith("HTTP/1.1 405", StringComparison.Ordinal), "socket refuses writes");
            Check(Send(port, "GET /data.json HTTP/1.1\r\nHost: example.com\r\n\r\n").StartsWith("HTTP/1.1 403", StringComparison.Ordinal), "socket refuses foreign host");
            // Not reachable on another local address of this machine (only the loopback address is bound).
            bool otherRefused = true;
            foreach (var addr in Dns.GetHostAddresses(Dns.GetHostName()))
            {
                if (addr.AddressFamily != AddressFamily.InterNetwork || IPAddress.IsLoopback(addr)) continue;
                try
                {
                    using (var c = new TcpClient()) { var t = c.BeginConnect(addr, port, null, null); if (t.AsyncWaitHandle.WaitOne(1500) && c.Connected) otherRefused = false; }
                }
                catch (SocketException) { }
            }
            Check(otherRefused, "not reachable from network addresses");
        }
        finally { server.Dispose(); }
    }

    static void OptionsParsing()
    {
        var o = Options.Parse(new[] { "--provision", "--edition", "Dev", "--pawnio", "--result", @"C:\x\sensor-setup-result.ini" });
        Check(o.Mode == "provision" && o.Edition == HelperEdition.Dev && o.PawnIo && o.Result.EndsWith("ini", StringComparison.Ordinal), "provision options");
        Check(Options.Parse(new[] { "--version" }).Mode == "version", "version");
        Throws<ArgumentException>(() => Options.Parse(new string[0]), "mode required");
        Throws<ArgumentException>(() => Options.Parse(new[] { "--service" }), "edition required");
        Throws<ArgumentException>(() => Options.Parse(new[] { "--service", "--edition", "Other" }), "unknown edition");
        Throws<ArgumentException>(() => Options.Parse(new[] { "--service", "--console", "--edition", "Dev" }), "one mode only");
        Throws<ArgumentException>(() => Options.Parse(new[] { "--service", "--edition", "Dev", "--pawnio" }), "pawnio only with provision");
        Throws<ArgumentException>(() => Options.Parse(new[] { "--service", "--edition", "Dev", "--port", "80" }), "no port override");
        Throws<ArgumentException>(() => Options.Parse(new[] { "--provision", "--edition", "Dev", "--unrestricted" }), "unrestricted is not an option");
    }

    static void Quoting()
    {
        Check(Provisioner.QuoteArg("plain") == "plain", "plain");
        Check(Provisioner.QuoteArg("a b") == "\"a b\"", "space");
        Check(Provisioner.QuoteArg("say \"hi\"") == "\"say \\\"hi\\\"\"", "inner quotes");
        Check(Provisioner.QuoteArg(@"C:\dir with space\") == "\"C:\\dir with space\\\\\"", "trailing backslash doubled");
        Check(Provisioner.QuoteArg("") == "\"\"", "empty");
    }

    static void ServiceArguments()
    {
        string exe = @"C:\Program Files\Ultra Suite Sensor Helper\UltraSuiteSensorHelper.exe";
        Check(Provisioner.ServiceCommandLine(exe, HelperEdition.Public) == "\"" + exe + "\" --service --edition Public", "service command line");
        string create = Provisioner.ScCreateArguments(false, HelperEdition.Public, exe);
        Check(create == "create UltraSuiteSensorHelper binPath= \"\\\"" + exe + "\\\" --service --edition Public\" start= delayed-auto obj= LocalSystem DisplayName= \"Ultra Suite Sensor Helper\"", "sc create");
        Check(Provisioner.ScCreateArguments(true, HelperEdition.Dev, exe).StartsWith("config UltraSuiteSensorHelperDev binPath= ", StringComparison.Ordinal), "sc config for an existing Dev service");
        Check(!create.Contains("unrestricted") && !PawnIoPin.InstallArguments.Contains("unrestricted"), "never unrestricted");
        Check(PawnIoPin.InstallArguments == "-install -silent", "silent install arguments");
    }

    static void ResultPaths()
    {
        string dir = Path.Combine(Path.GetTempPath(), "ush-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        try
        {
            Check(Provisioner.ValidResultPath(Path.Combine(dir, "sensor-setup-result.ini")), "valid result path");
            Check(!Provisioner.ValidResultPath(Path.Combine(dir, "other.ini")), "other file names refused");
            Check(!Provisioner.ValidResultPath("sensor-setup-result.ini"), "relative path refused");
            Check(!Provisioner.ValidResultPath(Path.Combine(dir, "missing", "sensor-setup-result.ini")), "missing folder refused");
            Check(!Provisioner.ValidResultPath(null), "null refused");
            var r = new ProvisionResult { Elevated = true, PawnIo = "installed", PawnIoExit = 3010, Restart = true, Service = "running", Message = "line1\nline2" };
            Provisioner.WriteResult(Path.Combine(dir, "sensor-setup-result.ini"), r);
            Check(File.ReadAllText(Path.Combine(dir, "sensor-setup-result.ini")).Contains("restart=1"), "result written");
            Provisioner.WriteResult(Path.Combine(dir, "evil.ini"), r);
            Check(!File.Exists(Path.Combine(dir, "evil.ini")), "invalid result path not written");
        }
        finally { Directory.Delete(dir, true); }
    }

    static void PawnIoDecisions()
    {
        Check(Provisioner.PawnIoAction(null) == "install", "missing PawnIO installs");
        Check(Provisioner.PawnIoAction(new Version("2.1.0")) == "install", "older PawnIO updates");
        Check(Provisioner.PawnIoAction(new Version("2.2.0.0")) == "present", "same version skipped");
        Check(Provisioner.PawnIoAction(new Version("2.2")) == "present", "same version, short form");
        Check(Provisioner.PawnIoAction(new Version("3.0.0.0")) == "present", "newer version skipped");
        bool restart;
        Check(Provisioner.InstallerSucceeded(0, out restart) && !restart, "exit 0");
        Check(Provisioner.InstallerSucceeded(3010, out restart) && restart, "exit 3010 needs restart");
        Check(!Provisioner.InstallerSucceeded(1, out restart), "exit 1 fails");
        Check(!Provisioner.InstallerSucceeded(1223, out restart), "cancelled fails");
    }

    static void DriverClassification()
    {
        Check(DriverStatus.Classify(true, 0) == "ok", "opened");
        Check(DriverStatus.Classify(true, 2) == "not_running", "installed but no device");
        Check(DriverStatus.Classify(false, 2) == "not_installed", "not installed");
        Check(DriverStatus.Classify(true, 5) == "access_denied", "access denied");
        Check(DriverStatus.Classify(true, 1) == "error", "other error");
        var json = new DriverReport { Installed = true, InstalledVersion = "2.2.0.0", Device = "ok", MemoryIntegrity = true }.ToJson();
        Check(json == "{\"installed\":true,\"version\":\"2.2.0.0\",\"device\":\"ok\",\"device_error\":0,\"memory_integrity\":true}", "driver json");
    }

    static void Hashing()
    {
        Check(Provisioner.Sha256Hex(new MemoryStream(Encoding.ASCII.GetBytes("abc"))) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "sha256");
        Check(PawnIoPin.Sha256.Length == 64 && PawnIoPin.Url.StartsWith("https://github.com/namazso/PawnIO.Setup/releases/download/", StringComparison.Ordinal), "pin shape");
    }

    static void ResultIni()
    {
        string ini = new ProvisionResult { Elevated = false, Message = "a\r\nb" }.ToIni();
        Check(ini.StartsWith("[result]\r\nelevated=0\r\npawnio=skipped\r\n", StringComparison.Ordinal) && ini.Contains("message=a  b\r\n"), "ini");
    }

    static void EditionsAreIsolated()
    {
        Check(HelperEdition.Public.Port == 8095 && HelperEdition.Dev.Port == 18095, "ports");
        Check(HelperEdition.Public.ServiceName != HelperEdition.Dev.ServiceName, "service names differ");
        Check(HelperEdition.Public.InstallDirectory(@"C:\PF") != HelperEdition.Dev.InstallDirectory(@"C:\PF"), "folders differ");
        Check(HelperEdition.Dev.DisplayName.EndsWith("(Dev)", StringComparison.Ordinal), "Dev labeled");
    }
}
