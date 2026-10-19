// widget.json (from Tavinor) -> the card's data: plain strings, empty when
// there's nothing to show. No Windows dependencies, so it can be tested.
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TavinorWidget
{
    public static class CardData
    {
    // The card's data: plain strings, empty when there's nothing to show,
    // so the template only has to hide empty lines.
    // `now`: for the focus countdown; `status`: the last button's error, if any.
    public static string Build(string? json, DateTime? now = null, string status = "")
    {
        var d = new JsonObject { ["status"] = status };
        JsonNode? s;
        try { s = json == null ? null : JsonNode.Parse(json); }
        catch (Exception) { s = null; }
        if (s == null)
        {
            d["note"] = "Open Tavinor to start the widget";
            return Finish(d);
        }
        string Str(JsonNode? n, string key) => n?[key]?.ToString() ?? "";

        var w = s["weather"];
        if (w != null && w["error"] != null) d["note"] = Str(w, "error");
        else if (w != null)
        {
            d["icon"] = Str(w, "icon");
            d["temp"] = Str(w, "temp") + "°";
            d["summary"] = Str(w, "summary") + " · " + Str(w, "place");
            d["short"] = Str(w, "summary");                  // small size: no room for the place
            d["range"] = $"H {Str(w, "high")}° · L {Str(w, "low")}° · {Str(w, "rain")}% rain";
            if (w["feels"] != null)
                d["detail"] = $"Feels {Str(w, "feels")}° · {Str(w, "humidity")}% humidity · {Str(w, "wind")} {Str(w, "wind_unit")}";
            d["rangeShort"] = $"H {Str(w, "high")}° · L {Str(w, "low")}°";
            d["place"] = Str(w, "place");
            if (w["aqi"] != null && w["aqi"]!.GetValueKind() != JsonValueKind.Null)
            {
                d["air"] = $"Air quality {Str(w, "aqi")} · {Str(w, "aqi_label")}";
                d["aqiNum"] = Str(w, "aqi");
                d["aqiLabel"] = Str(w, "aqi_label");
            }
            if (w["sunrise"] != null)
                d["sun"] = $"Sunrise {Str(w, "sunrise")} · Sunset {Str(w, "sunset")}";
        }
        var m = s["meeting"];
        if (m != null)
        {
            d["meeting"] = Str(m, "title") == "" ? $"Meeting at {Str(m, "time")}" : $"{Str(m, "time")} · {Str(m, "title")}";
            d["meetingShort"] = $"Meeting {Str(m, "time")}";
            d["meetingTime"] = Str(m, "time");
            d["meetingTitle"] = Str(m, "title") == "" ? "Meeting" : Str(m, "title");
            d["meetingIn"] = Until(Str(m, "time"), now ?? DateTime.Now);
        }
        var t = s["tasks"];
        if (t != null)
        {
            int open = (int?)t["open"] ?? 0;
            d["tasks"] = open == 0 ? "No open tasks" : open == 1 ? "1 task open" : $"{open} tasks open";
            var top = t["top"]?.AsArray().Select(x => "• " + x).ToArray() ?? Array.Empty<string>();
            d["taskList"] = string.Join("\n", top);
            d["tasksNum"] = open.ToString();
            d["tasksWord"] = open == 1 ? "task open" : "tasks open";
            d["taskFirst"] = t["top"]?.AsArray().FirstOrDefault()?.ToString() ?? "";
            // The large size's to-do list, one row and a ✓ per task.
            var todo = new JsonArray();
            foreach (var x in t["list"]?.AsArray() ?? new JsonArray())
                todo.Add(new JsonObject { ["key"] = Str(x, "key"), ["text"] = Str(x, "text") });
            d["todo"] = todo;
            d["todoAny"] = todo.Count > 0 ? "1" : "";
        }
        var f = s["focus"];
        if (f != null)
        {
            // Counted down from `ends` each minute; widget.json is only rewritten every 5.
            var left = Str(f, "minutes_left");
            bool over = false;
            if (DateTime.TryParse(Str(f, "ends"), out var ends))
            {
                var mins = Math.Ceiling((ends - (now ?? DateTime.Now)).TotalMinutes);
                over = mins <= 0;
                left = mins.ToString("0");
            }
            if (!over)
            {
                d["focus"] = $"Focus · {left} min left" + (Str(f, "task") == "" ? "" : " · " + Str(f, "task"));
                d["focusOn"] = "1";
                d["focusLeft"] = left;
                d["focusTask"] = Str(f, "task");
            }
        }
        var h = s["habits"];
        if (h != null) d["habits"] = $"Habits · {Str(h, "done")} of {Str(h, "of")} today";
        d["updated"] = "Tavinor · " + Str(s, "date") + " · " + Str(s, "updated");
        return Finish(d);
    }

    static string Finish(JsonObject d)
    {
        foreach (var k in new[] { "icon", "temp", "summary", "short", "range", "detail", "air", "sun", "meeting", "meetingShort",
                                  "tasks", "taskList", "todoAny", "focus", "focusOn", "habits", "note", "status", "updated",
                                  // the small one-thing widgets
                                  "rangeShort", "place", "aqiNum", "aqiLabel", "meetingTime", "meetingTitle", "meetingIn",
                                  "tasksNum", "tasksWord", "taskFirst", "focusLeft", "focusTask" })
            d[k] ??= "";
        d["todo"] ??= new JsonArray();
        return d.ToJsonString();
    }

    // "in 40 min", "in 2 h 5 min", "now" until a meeting at HH:MM today.
    static string Until(string hhmm, DateTime now)
    {
        if (!TimeSpan.TryParse(hhmm, out var at)) return "";
        var mins = (int)Math.Ceiling((now.Date + at - now).TotalMinutes);
        if (mins <= 0) return "now";
        return mins < 60 ? $"in {mins} min" : mins % 60 == 0 ? $"in {mins / 60} h" : $"in {mins / 60} h {mins % 60} min";
    }

    // The Customize screen: the city now, and why the last one didn't save.
    public static string Customize(string? json, string status = "")
    {
        string city = "";
        try { city = JsonNode.Parse(json ?? "")?["weather"]?["place"]?.ToString() ?? ""; }
        catch (Exception) { }
        return new JsonObject { ["city"] = city, ["status"] = status }.ToJsonString();
    }
    }
}
