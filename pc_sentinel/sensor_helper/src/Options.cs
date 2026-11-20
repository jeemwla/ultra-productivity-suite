// Ultra Suite Sensor Helper: command-line options. Only fixed modes and the edition are accepted; there is no port,
// address or driver-mode override (the service always serves its edition's port on 127.0.0.1).
using System;

namespace UltraSuite.SensorHelper
{
    public sealed class Options
    {
        public string Mode;
        public HelperEdition Edition = HelperEdition.Public;
        public bool PawnIo;
        public string Result;

        public static Options Parse(string[] args)
        {
            var o = new Options();
            bool edition = false;
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                switch (a)
                {
                    case "--service": case "--console": case "--provision": case "--unprovision": case "--version":
                        if (o.Mode != null) throw new ArgumentException("Only one mode may be given.");
                        o.Mode = a.Substring(2);
                        break;
                    case "--edition":
                        if (++i >= args.Length) throw new ArgumentException("--edition needs Public or Dev.");
                        o.Edition = HelperEdition.Parse(args[i]);
                        edition = true;
                        break;
                    case "--pawnio":
                        o.PawnIo = true;
                        break;
                    case "--result":
                        if (++i >= args.Length) throw new ArgumentException("--result needs a path.");
                        o.Result = args[i];
                        break;
                    default:
                        throw new ArgumentException("Unknown argument: " + a);
                }
            }
            if (o.Mode == null) throw new ArgumentException("Give --service, --console, --provision, --unprovision or --version.");
            if (o.Mode != "version" && !edition) throw new ArgumentException("--edition is required.");
            if (o.PawnIo && o.Mode != "provision") throw new ArgumentException("--pawnio is only used with --provision.");
            if (o.Result != null && o.Mode != "provision" && o.Mode != "unprovision")
                throw new ArgumentException("--result is only used with --provision or --unprovision.");
            return o;
        }
    }
}
