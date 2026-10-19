// The widget's buttons -> Tavinor, over its bridge with the widget's own token
// (widget-link.json, rewritten each time Tavinor starts). If Tavinor isn't
// running, start it and try again.
using System.Diagnostics;
using System.Text;
using System.Text.Json.Nodes;

namespace TavinorWidget
{
    static class Link
    {
        static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(8) };

        static string LinkFile => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tavinor", "widget-link.json");

        // Null when Tavinor did it, else what to show on the card.
        public static string? Send(string tool, JsonObject args)
        {
            var err = TrySend(tool, args, out bool reached);
            if (reached) return err;
            if (Running()) return "Tavinor is busy, try again in a moment";
            if (!StartTavinor()) return "Couldn't open Tavinor";
            for (int i = 0; i < 40; i++)                     // up to 20 s to start
            {
                Thread.Sleep(500);
                err = TrySend(tool, args, out reached);
                if (reached) return err;
            }
            return "Tavinor didn't answer, open it and try again";
        }

        static string? TrySend(string tool, JsonObject args, out bool reached)
        {
            reached = false;
            try
            {
                var link = JsonNode.Parse(File.ReadAllText(LinkFile))!;
                var body = new JsonObject { ["tool"] = tool, ["args"] = args }.ToJsonString();
                using var req = new HttpRequestMessage(HttpMethod.Post, $"http://127.0.0.1:{(int)link["port"]!}/")
                    { Content = new StringContent(body, Encoding.UTF8, "application/json") };
                req.Headers.Add("X-Tavinor-Widget", link["token"]!.ToString());
                using var resp = Http.Send(req);
                if (!resp.IsSuccessStatusCode) return null;   // an old link file: not our Tavinor
                reached = true;
                var res = JsonNode.Parse(resp.Content.ReadAsStream())!;
                return (bool?)res["ok"] == true ? null : res["error"]?.ToString() ?? "Tavinor couldn't do that";
            }
            catch (Exception) { return null; }              // no file, or nothing listening
        }

        // Tavinor's process is up but its bridge didn't answer: don't start a second one.
        static bool Running()
        {
            try
            {
                var pid = (int)JsonNode.Parse(File.ReadAllText(LinkFile))!["pid"]!;
                return Process.GetProcessById(pid).ProcessName.Equals("Tavinor", StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        static bool StartTavinor()
        {
            // Widget\TavinorWidget.exe and Tavinor\Tavinor.exe sit side by side in the package.
            var exe = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "Tavinor", "Tavinor.exe"));
            if (!File.Exists(exe)) return false;
            try { Process.Start(new ProcessStartInfo(exe) { UseShellExecute = false }); return true; }
            catch (Exception) { return false; }
        }
    }
}
