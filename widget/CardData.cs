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
    public static string Build(string? json)
    {
        var d = new JsonObject();
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
            if (w["aqi"] != null && w["aqi"]!.GetValueKind() != JsonValueKind.Null)
                d["air"] = $"Air quality {Str(w, "aqi")} · {Str(w, "aqi_label")}";
            if (w["sunrise"] != null)
                d["sun"] = $"Sunrise {Str(w, "sunrise")} · Sunset {Str(w, "sunset")}";
        }
        var m = s["meeting"];
        if (m != null)
        {
            d["meeting"] = Str(m, "title") == "" ? $"Meeting at {Str(m, "time")}" : $"{Str(m, "time")} · {Str(m, "title")}";
            d["meetingShort"] = $"Meeting {Str(m, "time")}";
        }
        var t = s["tasks"];
        if (t != null)
        {
            int open = (int?)t["open"] ?? 0;
            d["tasks"] = open == 0 ? "No open tasks" : open == 1 ? "1 task open" : $"{open} tasks open";
            var top = t["top"]?.AsArray().Select(x => "• " + x).ToArray() ?? Array.Empty<string>();
            d["taskList"] = string.Join("\n", top);
        }
        var f = s["focus"];
        if (f != null)
            d["focus"] = $"Focus · {Str(f, "minutes_left")} min left" + (Str(f, "task") == "" ? "" : " · " + Str(f, "task"));
        var h = s["habits"];
        if (h != null) d["habits"] = $"Habits · {Str(h, "done")} of {Str(h, "of")} today";
        d["updated"] = "Tavinor · " + Str(s, "date") + " · " + Str(s, "updated");
        return Finish(d);
    }

    static string Finish(JsonObject d)
    {
        foreach (var k in new[] { "icon", "temp", "summary", "short", "range", "detail", "air", "sun", "meeting", "meetingShort",
                                  "tasks", "taskList", "focus", "habits", "note", "updated" })
            d[k] ??= "";
        return d.ToJsonString();
    }
    }
}
