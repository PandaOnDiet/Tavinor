// Tavinor's widget: reads widget.json (written by Tavinor, see widget.py),
// turns it into ready-to-show lines, and hands Windows an Adaptive Card.
// Refreshes when the file changes, and every minute (the focus countdown).
// Its buttons go to Tavinor through Link; Customize sets the city.
using System.Reflection;
using System.Text.Json.Nodes;
using Microsoft.Windows.Widgets.Providers;

namespace TavinorWidget
{
    internal class WidgetProvider : IWidgetProvider, IWidgetProvider2
    {
        public const string ClassId = "67F13B41-3FA5-451A-B1FB-EF8590EBE48D";
        public static readonly ManualResetEvent NoWidgetsLeft = new(false);

        static readonly Dictionary<string, bool> Running = new();   // widget id -> active
        static readonly object Gate = new();
        static readonly string Card = ReadCard("card.json");
        static readonly string CustomizeCard = ReadCard("customize.json");
        static readonly HashSet<string> Customizing = new();             // widget ids showing the city screen
        static readonly Dictionary<string, string> Status = new();       // widget id -> last error, shown on the card
        static FileSystemWatcher? watcher;
        static Timer? timer;

        // Same folder Tavinor writes to when installed (tavinor.DATA).
        static string DataFile => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tavinor", "widget.json");

        public WidgetProvider()
        {
            // After a restart or crash, pick up the widgets still pinned.
            foreach (var info in WidgetManager.GetDefault().GetWidgetInfos())
                lock (Gate) Running[info.WidgetContext.Id] = false;
            StartWatching();
        }

        public void CreateWidget(WidgetContext context)
        {
            lock (Gate) Running[context.Id] = true;
            Update(context.Id);
        }

        public void DeleteWidget(string widgetId, string customState)
        {
            lock (Gate)
            {
                Running.Remove(widgetId);
                Customizing.Remove(widgetId);
                Status.Remove(widgetId);
                if (Running.Count == 0) NoWidgetsLeft.Set();
            }
        }

        public void OnActionInvoked(WidgetActionInvokedArgs args)
        {
            var id = args.WidgetContext.Id;
            var verb = args.Verb;
            JsonNode? data;
            try { data = string.IsNullOrEmpty(args.Data) ? null : JsonNode.Parse(args.Data); }
            catch (Exception) { data = null; }
            string Arg(string k) => data?[k]?.ToString() ?? "";
            // Off the widget host's thread: starting Tavinor can take seconds.
            Task.Run(() =>
            {
                string? err = verb switch
                {
                    "add" => Link.Send("add", new JsonObject { ["text"] = Arg("text") }),
                    "done" => Link.Send("done", new JsonObject { ["key"] = Arg("key") }),
                    "focus" => Link.Send("focus", new JsonObject { ["minutes"] = int.TryParse(Arg("minutes"), out var m) ? m : 25 }),
                    "stop" => Link.Send("stop", new JsonObject()),
                    "savePlace" => Link.Send("place", new JsonObject { ["value"] = Arg("city") }),
                    _ => null,                                  // "cancel", or a plain refresh
                };
                lock (Gate)
                {
                    Status[id] = err ?? "";
                    if (verb == "cancel" || (verb == "savePlace" && err == null)) Customizing.Remove(id);
                }
                Update(id);
            });
        }

        public void OnCustomizationRequested(WidgetCustomizationRequestedArgs args)
        {
            lock (Gate) { Customizing.Add(args.WidgetContext.Id); Status[args.WidgetContext.Id] = ""; }
            Update(args.WidgetContext.Id);
        }

        public void OnWidgetContextChanged(WidgetContextChangedArgs args) => Update(args.WidgetContext.Id);

        public void Activate(WidgetContext context)
        {
            lock (Gate) Running[context.Id] = true;
            Update(context.Id);
        }

        public void Deactivate(string widgetId)
        {
            lock (Gate) if (Running.ContainsKey(widgetId)) Running[widgetId] = false;
        }

        static void StartWatching()
        {
            if (watcher != null) return;
            var dir = Path.GetDirectoryName(DataFile)!;
            Directory.CreateDirectory(dir);
            watcher = new FileSystemWatcher(dir, "widget.json") {
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName };
            watcher.Changed += (_, _) => UpdateAll();
            watcher.Renamed += (_, _) => UpdateAll();    // Tavinor writes a .tmp then renames it
            watcher.EnableRaisingEvents = true;
            timer = new Timer(_ => UpdateAll(), null, TimeSpan.FromMinutes(1), TimeSpan.FromMinutes(1));
        }

        static void UpdateAll()
        {
            string[] ids;
            lock (Gate) ids = Running.Keys.ToArray();
            foreach (var id in ids) Update(id);
        }

        static void Update(string widgetId)
        {
            try
            {
                bool customizing;
                string status;
                lock (Gate)
                {
                    customizing = Customizing.Contains(widgetId);
                    status = Status.GetValueOrDefault(widgetId, "");
                }
                var options = customizing
                    ? new WidgetUpdateRequestOptions(widgetId) { Template = CustomizeCard, Data = CardData.Customize(ReadData(), status) }
                    : new WidgetUpdateRequestOptions(widgetId) { Template = Card, Data = CardData.Build(ReadData(), DateTime.Now, status) };
                WidgetManager.GetDefault().UpdateWidget(options);
            }
            catch (Exception) { /* the host went away mid-update: the next one will do */ }
        }

        static string? ReadData()
        {
            try { return File.ReadAllText(DataFile); }
            catch (Exception) { return null; }               // not written yet, or mid-rename
        }

        static string ReadCard(string name)
        {
            using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("TavinorWidget." + name)!;
            return new StreamReader(s).ReadToEnd();
        }
    }
}
