// Ultra Suite Sensor Helper: a small read-only HTTP server on 127.0.0.1.
// It binds the IPv4 loopback address only (never 0.0.0.0 or a network adapter), answers GET and HEAD for two paths
// (/data.json and /health), and refuses everything else. There are no write, fan-control or settings endpoints.
// Requests whose Host header is not a loopback name are refused, so a web page cannot read the sensors through DNS
// rebinding, and no CORS headers are sent. Header size, request time and concurrent connections are bounded.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace UltraSuite.SensorHelper
{
    public sealed class RequestHead
    {
        public string Method, Target, Path, Version, Host;
        public bool Valid;
        public int Error; // HTTP status for an invalid request

        public const int MaxHeaderBytes = 8192;

        /// <summary>Parses the request line and headers (text up to the blank line).</summary>
        public static RequestHead Parse(string head)
        {
            var r = new RequestHead { Error = 400 };
            if (string.IsNullOrEmpty(head) || head.Length > MaxHeaderBytes) { r.Error = 431; return r; }
            string[] lines = head.Split(new[] { "\r\n" }, StringSplitOptions.None);
            string[] parts = lines[0].Split(' ');
            if (parts.Length != 3) return r;
            r.Method = parts[0]; r.Target = parts[1]; r.Version = parts[2];
            if (r.Version != "HTTP/1.1" && r.Version != "HTTP/1.0") { r.Error = 505; return r; }
            if (r.Target.Length == 0 || r.Target[0] != '/') return r;
            int q = r.Target.IndexOf('?');
            r.Path = q >= 0 ? r.Target.Substring(0, q) : r.Target;
            int hosts = 0;
            for (int i = 1; i < lines.Length; i++)
            {
                string line = lines[i];
                if (line.Length == 0) continue;
                int colon = line.IndexOf(':');
                if (colon <= 0) return r;
                string name = line.Substring(0, colon).Trim();
                if (name.Length != line.Substring(0, colon).Length) return r; // no whitespace before the colon
                if (string.Equals(name, "Host", StringComparison.OrdinalIgnoreCase))
                {
                    hosts++;
                    r.Host = line.Substring(colon + 1).Trim();
                }
            }
            if (hosts > 1) return r;
            if (hosts == 0 && r.Version == "HTTP/1.1") return r;
            r.Valid = true;
            r.Error = 0;
            return r;
        }

        /// <summary>True when the Host header names this machine's loopback address (or is absent on HTTP/1.0).</summary>
        public static bool LoopbackHost(string host, int port)
        {
            if (host == null) return true;
            string h = host.ToLowerInvariant();
            string p = port.ToString(CultureInfo.InvariantCulture);
            foreach (string name in new[] { "127.0.0.1", "localhost", "[::1]" })
                if (h == name || h == name + ":" + p) return true;
            return false;
        }
    }

    public sealed class Response
    {
        public int Status;
        public string ContentType = "application/json; charset=utf-8";
        public string Body = "";
        public string Allow;

        public static string Reason(int status)
        {
            switch (status)
            {
                case 200: return "OK";
                case 400: return "Bad Request";
                case 403: return "Forbidden";
                case 404: return "Not Found";
                case 405: return "Method Not Allowed";
                case 408: return "Request Timeout";
                case 431: return "Request Header Fields Too Large";
                case 503: return "Service Unavailable";
                case 505: return "HTTP Version Not Supported";
                default: return "Error";
            }
        }

        public byte[] Serialize(bool includeBody)
        {
            byte[] body = Encoding.UTF8.GetBytes(Body ?? "");
            var sb = new StringBuilder();
            sb.Append("HTTP/1.1 ").Append(Status.ToString(CultureInfo.InvariantCulture)).Append(' ').Append(Reason(Status)).Append("\r\n");
            sb.Append("Content-Type: ").Append(ContentType).Append("\r\n");
            sb.Append("Content-Length: ").Append(body.Length.ToString(CultureInfo.InvariantCulture)).Append("\r\n");
            sb.Append("Cache-Control: no-store\r\n");
            sb.Append("X-Content-Type-Options: nosniff\r\n");
            if (Allow != null) sb.Append("Allow: ").Append(Allow).Append("\r\n");
            sb.Append("Connection: close\r\n\r\n");
            byte[] head = Encoding.ASCII.GetBytes(sb.ToString());
            if (!includeBody) return head;
            var all = new byte[head.Length + body.Length];
            Buffer.BlockCopy(head, 0, all, 0, head.Length);
            Buffer.BlockCopy(body, 0, all, head.Length, body.Length);
            return all;
        }
    }

    public sealed class Router
    {
        readonly int _port;
        readonly Func<string> _data;
        readonly Func<string> _health;

        public Router(int port, Func<string> data, Func<string> health)
        {
            _port = port; _data = data; _health = health;
        }

        public Response Handle(RequestHead req)
        {
            if (req == null || !req.Valid) return Error(req == null ? 400 : req.Error, "bad request");
            if (!RequestHead.LoopbackHost(req.Host, _port)) return Error(403, "only local requests are answered");
            bool known = req.Path == "/data.json" || req.Path == "/health";
            if (!known) return Error(404, "not found");
            if (req.Method != "GET" && req.Method != "HEAD")
            {
                var r = Error(405, "read-only");
                r.Allow = "GET, HEAD";
                return r;
            }
            if (req.Path == "/health") return new Response { Status = 200, Body = _health() };
            string data = _data();
            if (data == null) return Error(503, "sensors are starting");
            return new Response { Status = 200, Body = data };
        }

        static Response Error(int status, string message)
        {
            return new Response { Status = status, Body = "{\"error\":" + TreeJson.Quote(message) + "}" };
        }
    }

    public sealed class LoopbackServer : IDisposable
    {
        const int MaxConnections = 8;
        const int TimeoutMs = 5000;
        readonly TcpListener _listener;
        readonly Router _router;
        readonly SemaphoreSlim _slots = new SemaphoreSlim(MaxConnections, MaxConnections);
        Thread _thread;
        volatile bool _stopping;

        public LoopbackServer(int port, Router router)
        {
            // IPv4 loopback only. ExclusiveAddressUse stops another process from binding the same port alongside us.
            _listener = new TcpListener(IPAddress.Loopback, port) { ExclusiveAddressUse = true };
            _router = router;
        }

        public IPEndPoint LocalEndpoint { get { return (IPEndPoint)_listener.LocalEndpoint; } }

        public void Start()
        {
            _listener.Start(16);
            _thread = new Thread(AcceptLoop) { IsBackground = true, Name = "loopback-http" };
            _thread.Start();
        }

        void AcceptLoop()
        {
            while (!_stopping)
            {
                TcpClient client;
                try { client = _listener.AcceptTcpClient(); }
                catch (SocketException) { if (_stopping) return; continue; }
                catch (ObjectDisposedException) { return; }
                if (!_slots.Wait(0)) { client.Close(); continue; }
                ThreadPool.QueueUserWorkItem(_ => Serve(client));
            }
        }

        void Serve(TcpClient client)
        {
            try
            {
                using (client)
                {
                    client.ReceiveTimeout = TimeoutMs;
                    client.SendTimeout = TimeoutMs;
                    var remote = client.Client.RemoteEndPoint as IPEndPoint;
                    if (remote == null || !IPAddress.IsLoopback(remote.Address)) return;
                    var stream = client.GetStream();
                    string head = ReadHead(stream);
                    var req = head == null ? new RequestHead { Error = 431 } : RequestHead.Parse(head);
                    var resp = _router.Handle(req);
                    byte[] bytes = resp.Serialize(req.Method != "HEAD");
                    stream.Write(bytes, 0, bytes.Length);
                }
            }
            catch (Exception) { /* a dropped or slow client only loses its own request */ }
            finally { _slots.Release(); }
        }

        /// <summary>Reads up to the blank line that ends the headers; null when the headers are too large.</summary>
        public static string ReadHead(System.IO.Stream stream)
        {
            var buf = new List<byte>(1024);
            var one = new byte[1];
            while (buf.Count < RequestHead.MaxHeaderBytes)
            {
                int n = stream.Read(one, 0, 1);
                if (n <= 0) break;
                buf.Add(one[0]);
                int c = buf.Count;
                if (c >= 4 && buf[c - 4] == '\r' && buf[c - 3] == '\n' && buf[c - 2] == '\r' && buf[c - 1] == '\n')
                    return Encoding.ASCII.GetString(buf.ToArray(), 0, c - 4);
            }
            return buf.Count >= RequestHead.MaxHeaderBytes ? null : Encoding.ASCII.GetString(buf.ToArray());
        }

        public void Dispose()
        {
            _stopping = true;
            try { _listener.Stop(); } catch (Exception) { }
        }
    }
}
