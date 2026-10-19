// Tavinor's widget: reads widget.json (written by Tavinor, see widget.py),
// turns it into ready-to-show lines, and hands Windows an Adaptive Card.
// Refreshes when the file changes, and every 5 minutes as a backstop.
using System.Reflection;
using Microsoft.Windows.Widgets.Providers;

namespace TavinorWidget
{
    internal class WidgetProvider : IWidgetProvider
    {
        public const string ClassId = "67F13B41-3FA5-451A-B1FB-EF8590EBE48D";
        public static readonly ManualResetEvent NoWidgetsLeft = new(false);

        static readonly Dictionary<string, bool> Running = new();   // widget id -> active
        static readonly object Gate = new();
        static readonly string Card = ReadCard();
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
                if (Running.Count == 0) NoWidgetsLeft.Set();
            }
        }

        public void OnActionInvoked(WidgetActionInvokedArgs args) => Update(args.WidgetContext.Id);

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
            timer = new Timer(_ => UpdateAll(), null, TimeSpan.FromMinutes(5), TimeSpan.FromMinutes(5));
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
                var options = new WidgetUpdateRequestOptions(widgetId) { Template = Card, Data = CardData.Build(ReadData()) };
                WidgetManager.GetDefault().UpdateWidget(options);
            }
            catch (Exception) { /* the host went away mid-update: the next one will do */ }
        }

        static string? ReadData()
        {
            try { return File.ReadAllText(DataFile); }
            catch (Exception) { return null; }               // not written yet, or mid-rename
        }

        static string ReadCard()
        {
            using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("TavinorWidget.card.json")!;
            return new StreamReader(s).ReadToEnd();
        }
    }
}
