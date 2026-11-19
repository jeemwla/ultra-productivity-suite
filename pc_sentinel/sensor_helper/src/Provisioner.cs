// Ultra Suite Sensor Helper: one-time elevated setup and clean removal.
//
// Why a Windows service: PawnIO only lets administrators and SYSTEM open its device, so the reader needs elevation
// every time it runs. A LocalSystem service starts at boot without a logon, keeps working for standard (non-admin)
// accounts, is restarted by Windows if it stops, and is removed cleanly with one "sc delete". A logon task with
// highest privileges would only elevate for administrator accounts and only after they sign in.
//
// Why a copy under Program Files: the suite installs per user (%LOCALAPPDATA%), which any program running as the user
// can change. A service that runs files from there would let such a program gain SYSTEM rights. The service runs only
// the copy in "%ProgramFiles%\Ultra Suite Sensor Helper", where only administrators can write.
//
// The PawnIO installer is downloaded from its official GitHub release by this elevated process into that protected
// folder, checked against the pinned size and SHA-256 while the file is held open read-only, then run with
// "-install -silent" (never "-unrestricted"). Exit codes 0 and 3010 are success; 3010 means a restart is needed.
// PawnIO is never uninstalled here: other programs (for example FanControl and LibreHardwareMonitor) rely on it.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;

namespace UltraSuite.SensorHelper
{
    public sealed class ProvisionResult
    {
        public bool Elevated;
        public string PawnIo = "skipped";   // present | installed | skipped | download_failed | hash_mismatch | install_failed | timeout
        public int PawnIoExit = -1;
        public bool Restart;
        public string Service = "not_set_up"; // running | start_failed | create_failed | copy_failed | removed | remove_failed
        public string Message = "";

        public string ToIni()
        {
            var sb = new StringBuilder();
            sb.Append("[result]\r\n");
            sb.Append("elevated=").Append(Elevated ? "1" : "0").Append("\r\n");
            sb.Append("pawnio=").Append(PawnIo).Append("\r\n");
            sb.Append("pawnio_exit=").Append(PawnIoExit.ToString(CultureInfo.InvariantCulture)).Append("\r\n");
            sb.Append("restart=").Append(Restart ? "1" : "0").Append("\r\n");
            sb.Append("service=").Append(Service).Append("\r\n");
            sb.Append("message=").Append(OneLine(Message)).Append("\r\n");
            return sb.ToString();
        }

        static string OneLine(string s)
        {
            return (s ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
        }
    }

    public static class Provisioner
    {
        public const string ResultFileName = "sensor-setup-result.ini";
        const string ExeName = "UltraSuiteSensorHelper.exe";
        static readonly string[] PayloadExtensions = { ".exe", ".dll", ".config", ".txt" };

        public static bool IsElevated()
        {
            using (var id = WindowsIdentity.GetCurrent())
                return new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
        }

        public static string ProgramFiles()
        {
            // The 64-bit Program Files folder, also when this process runs 32-bit.
            string p = Environment.GetEnvironmentVariable("ProgramW6432");
            return string.IsNullOrEmpty(p) ? Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) : p;
        }

        /// <summary>The result file must be named exactly sensor-setup-result.ini in an existing, ordinary folder.</summary>
        public static bool ValidResultPath(string path)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || !Path.IsPathRooted(path)) return false;
                if (!string.Equals(Path.GetFileName(path), ResultFileName, StringComparison.OrdinalIgnoreCase)) return false;
                var dir = new DirectoryInfo(Path.GetDirectoryName(path));
                return dir.Exists && (dir.Attributes & FileAttributes.ReparsePoint) == 0;
            }
            catch (Exception) { return false; }
        }

        /// <summary>What to do about PawnIO: "present" when the pinned version or newer is installed, else "install".</summary>
        public static string PawnIoAction(Version installed)
        {
            return DriverStatus.SatisfiesPin(installed) ? "present" : "install";
        }

        public static bool InstallerSucceeded(int exitCode, out bool restart)
        {
            restart = exitCode == 3010;
            return exitCode == 0 || exitCode == 3010;
        }

        /// <summary>Quotes one argument for the Windows command-line parser (CommandLineToArgvW rules).</summary>
        public static string QuoteArg(string arg)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
            var sb = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { sb.Append('\\', slashes * 2 + 1).Append('"'); slashes = 0; continue; }
                sb.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            sb.Append('\\', slashes * 2).Append('"');
            return sb.ToString();
        }

        /// <summary>The service command line: the quoted Program Files exe plus fixed, edition-only arguments.</summary>
        public static string ServiceCommandLine(string exePath, HelperEdition edition)
        {
            return "\"" + exePath + "\" --service --edition " + edition.Name;
        }

        /// <summary>sc.exe arguments to create (or reconfigure) the service.</summary>
        public static string ScCreateArguments(bool exists, HelperEdition edition, string exePath)
        {
            return (exists ? "config " : "create ") + edition.ServiceName
                + " binPath= " + QuoteArg(ServiceCommandLine(exePath, edition))
                + " start= delayed-auto obj= LocalSystem DisplayName= " + QuoteArg(edition.DisplayName);
        }

        public static string Sha256Hex(Stream s)
        {
            using (var sha = SHA256.Create())
            {
                byte[] h = sha.ComputeHash(s);
                var sb = new StringBuilder(64);
                foreach (byte b in h) sb.Append(b.ToString("x2", CultureInfo.InvariantCulture));
                return sb.ToString();
            }
        }

        public static ProvisionResult Provision(HelperEdition edition, bool installPawnIo, string sourceDir, Action<string> log)
        {
            var r = new ProvisionResult { Elevated = IsElevated() };
            if (!r.Elevated)
            {
                r.Message = "Administrator permission was not given, so full sensors were not set up.";
                return r;
            }
            string target = edition.InstallDirectory(ProgramFiles());
            Directory.CreateDirectory(target);

            if (installPawnIo) InstallPawnIo(target, r, log);
            else r.PawnIo = "skipped";

            try
            {
                StopService(edition, log);
                CopyPayload(sourceDir, target, log);
            }
            catch (Exception e)
            {
                r.Service = "copy_failed";
                r.Message = Join(r.Message, "The sensor helper files could not be copied: " + e.Message);
                return r;
            }
            string exe = Path.Combine(target, ExeName);
            bool exists = ServiceExists(edition);
            int code = RunSc(ScCreateArguments(exists, edition, exe), log);
            if (code != 0)
            {
                r.Service = "create_failed";
                r.Message = Join(r.Message, "Windows did not accept the sensor helper service (sc exit " + code + ").");
                return r;
            }
            RunSc("description " + edition.ServiceName + " " + QuoteArg(edition.Description), log);
            RunSc("failure " + edition.ServiceName + " reset= 86400 actions= restart/60000/restart/60000/restart/300000", log);
            r.Service = StartService(edition, log) ? "running" : "start_failed";
            if (r.Service == "start_failed")
                r.Message = Join(r.Message, "The sensor helper was set up but did not start yet. It starts again with Windows.");
            return r;
        }

        static string Join(string a, string b)
        {
            return string.IsNullOrEmpty(a) ? b : a + " " + b;
        }

        static void InstallPawnIo(string target, ProvisionResult r, Action<string> log)
        {
            string text;
            var installed = DriverStatus.InstalledVersion(out text);
            if (PawnIoAction(installed) == "present")
            {
                r.PawnIo = "present";
                log("PawnIO " + text + " is already installed; nothing to do.");
                return;
            }
            string downloads = Path.Combine(target, "download");
            Directory.CreateDirectory(downloads);
            string file = Path.Combine(downloads, "PawnIO_setup-" + PawnIoPin.Version + ".exe");
            try
            {
                if (File.Exists(file)) File.Delete(file);
                Download(PawnIoPin.Url, file, PawnIoPin.Size);
            }
            catch (Exception e)
            {
                r.PawnIo = "download_failed";
                r.Message = "The sensor driver (PawnIO) could not be downloaded (" + e.Message + "). PC Sentinel can try again later.";
                log(r.Message);
                TryDelete(file);
                return;
            }
            try
            {
                // Held open read-only (no writers, no delete) from the hash check until the installer has finished.
                using (var held = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (held.Length != PawnIoPin.Size || Sha256Hex(held) != PawnIoPin.Sha256)
                    {
                        r.PawnIo = "hash_mismatch";
                        r.Message = "The downloaded sensor driver did not match the official PawnIO " + PawnIoPin.Version
                            + " release, so it was not run.";
                        log(r.Message);
                        return;
                    }
                    var psi = new ProcessStartInfo(file, PawnIoPin.InstallArguments)
                    {
                        UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = downloads,
                    };
                    using (var p = Process.Start(psi))
                    {
                        if (!p.WaitForExit(10 * 60 * 1000))
                        {
                            r.PawnIo = "timeout";
                            r.Message = "The PawnIO installer is still running. Restart the PC after it finishes.";
                            log(r.Message);
                            return;
                        }
                        r.PawnIoExit = p.ExitCode;
                    }
                }
                bool restart;
                if (InstallerSucceeded(r.PawnIoExit, out restart))
                {
                    r.PawnIo = "installed";
                    r.Restart = restart;
                    if (restart) r.Message = "Restart the PC to finish installing the sensor driver.";
                }
                else
                {
                    r.PawnIo = "install_failed";
                    r.Message = "The PawnIO installer stopped with code " + r.PawnIoExit
                        + ". Windows may be blocking the driver. PC Sentinel keeps working with fewer sensors.";
                }
                log("PawnIO installer exit " + r.PawnIoExit);
            }
            finally
            {
                TryDelete(file);
            }
        }

        static void Download(string url, string file, long expectedSize)
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12 | (SecurityProtocolType)12288; // TLS 1.3
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 60000;
            req.ReadWriteTimeout = 60000;
            req.AllowAutoRedirect = true;
            req.UserAgent = "UltraSuiteSensorHelper";
            using (var resp = (HttpWebResponse)req.GetResponse())
            {
                // Only HTTPS hosts of the official GitHub release may serve the file.
                var host = resp.ResponseUri.Host.ToLowerInvariant();
                if (resp.ResponseUri.Scheme != "https" || !(host == "github.com" || host.EndsWith(".githubusercontent.com", StringComparison.Ordinal)))
                    throw new IOException("unexpected download host " + host);
                using (var input = resp.GetResponseStream())
                using (var output = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    var buf = new byte[81920];
                    long total = 0;
                    int n;
                    while ((n = input.Read(buf, 0, buf.Length)) > 0)
                    {
                        total += n;
                        if (total > expectedSize) throw new IOException("download larger than the official release");
                        output.Write(buf, 0, n);
                    }
                }
            }
        }

        static void CopyPayload(string sourceDir, string target, Action<string> log)
        {
            string src = Path.GetFullPath(sourceDir).TrimEnd('\\');
            string dst = Path.GetFullPath(target).TrimEnd('\\');
            if (string.Equals(src, dst, StringComparison.OrdinalIgnoreCase)) return; // repairing from the installed copy
            var wanted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var f in Directory.GetFiles(src))
            {
                if (Array.IndexOf(PayloadExtensions, Path.GetExtension(f).ToLowerInvariant()) < 0) continue;
                string name = Path.GetFileName(f);
                wanted.Add(name);
                File.Copy(f, Path.Combine(dst, name), true);
            }
            if (!wanted.Contains(ExeName)) throw new IOException(ExeName + " is missing from " + src);
            foreach (var f in Directory.GetFiles(dst))
            {
                if (Array.IndexOf(PayloadExtensions, Path.GetExtension(f).ToLowerInvariant()) < 0) continue;
                if (!wanted.Contains(Path.GetFileName(f))) TryDelete(f); // files a newer helper no longer ships
            }
            log("Copied " + wanted.Count + " files to " + dst);
        }

        public static ProvisionResult Unprovision(HelperEdition edition, Action<string> log)
        {
            var r = new ProvisionResult { Elevated = IsElevated(), PawnIo = "kept" };
            if (!r.Elevated)
            {
                r.Service = "remove_failed";
                r.Message = "Administrator permission was not given, so the sensor helper service was left in place.";
                return r;
            }
            StopService(edition, log);
            if (ServiceExists(edition) && RunSc("delete " + edition.ServiceName, log) != 0)
            {
                r.Service = "remove_failed";
                r.Message = "Windows did not remove the sensor helper service.";
                return r;
            }
            string target = edition.InstallDirectory(ProgramFiles());
            string self = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory).TrimEnd('\\');
            if (Directory.Exists(target))
            {
                if (string.Equals(self, Path.GetFullPath(target).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                {
                    // Running from the folder being removed: remove it a moment after this process exits.
                    var psi = new ProcessStartInfo("cmd.exe", "/d /c ping -n 3 127.0.0.1 >nul & rmdir /s /q " + QuoteArg(target))
                    {
                        UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetTempPath(),
                    };
                    Process.Start(psi);
                }
                else
                {
                    try { Directory.Delete(target, true); }
                    catch (Exception e) { log("Could not remove " + target + ": " + e.Message); }
                }
            }
            r.Service = "removed";
            r.Message = "The sensor helper was removed. The PawnIO driver stays installed for other programs that use it.";
            return r;
        }

        static bool ServiceExists(HelperEdition edition)
        {
            foreach (var s in ServiceController.GetServices())
                using (s)
                    if (string.Equals(s.ServiceName, edition.ServiceName, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        static void StopService(HelperEdition edition, Action<string> log)
        {
            if (!ServiceExists(edition)) return;
            using (var sc = new ServiceController(edition.ServiceName))
            {
                try
                {
                    if (sc.Status != ServiceControllerStatus.Stopped && sc.Status != ServiceControllerStatus.StopPending) sc.Stop();
                    sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30));
                }
                catch (Exception e) { log("Stopping " + edition.ServiceName + ": " + e.Message); }
            }
        }

        static bool StartService(HelperEdition edition, Action<string> log)
        {
            try
            {
                using (var sc = new ServiceController(edition.ServiceName))
                {
                    if (sc.Status != ServiceControllerStatus.Running) sc.Start();
                    sc.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(30));
                    return true;
                }
            }
            catch (Exception e)
            {
                log("Starting " + edition.ServiceName + ": " + e.Message);
                return false;
            }
        }

        static int RunSc(string arguments, Action<string> log)
        {
            string sc = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "sc.exe");
            var psi = new ProcessStartInfo(sc, arguments)
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
            };
            using (var p = Process.Start(psi))
            {
                string output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
                p.WaitForExit(30000);
                log("sc " + arguments.Split(' ')[0] + ": exit " + p.ExitCode + " " + output.Trim());
                return p.ExitCode;
            }
        }

        static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch (Exception) { }
        }

        public static void WriteResult(string path, ProvisionResult r)
        {
            if (!ValidResultPath(path)) return;
            File.WriteAllText(path, r.ToIni(), new UTF8Encoding(false));
        }
    }
}
