using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using TavinorWidget;

// With a widget.json path: print the card data for it (used for previews).
if (args.Length == 1) { Console.WriteLine(CardData.Build(File.ReadAllText(args[0]))); return; }

int n = 0;
void Check(bool ok, string what) { if (!ok) throw new Exception("FAILED: " + what); n++; }
JsonObject Data(string? json) => JsonNode.Parse(CardData.Build(json))!.AsObject();
string S(JsonObject d, string k) => d[k]!.ToString();

const string Weather = "\"weather\": {\"place\": \"Noida\", \"temp\": 27, \"feels\": 29, \"humidity\": 59, \"wind\": 5, " +
    "\"wind_unit\": \"km/h\", \"summary\": \"Clear\", \"icon\": \"x\", \"high\": 29, \"low\": 20, \"rain\": 0, " +
    "\"aqi\": 191, \"aqi_label\": \"Unhealthy\"}";

var d = Data(null);
Check(S(d, "note") == "Open Tavinor to start the widget" && S(d, "temp") == "", "no file yet");
Check(Data("{not json")["note"]!.ToString().StartsWith("Open Tavinor"), "broken file");

d = Data("{\"updated\": \"17:30\", \"date\": \"Mon 19 Oct\", " + Weather + ", \"meeting\": {\"time\": \"15:00\", \"title\": \"\"}, " +
         "\"tasks\": {\"open\": 1, \"top\": []}, \"focus\": {\"minutes_left\": 12, \"task\": \"\"}}");
Check(S(d, "temp") == "27°" && S(d, "summary") == "Clear · Noida", "weather line");
Check(S(d, "range") == "H 29° · L 20° · 0% rain", "range line");
Check(S(d, "detail") == "Feels 29° · 59% humidity · 5 km/h" && S(d, "air") == "Air quality 191 · Unhealthy", "details");
Check(S(d, "sun") == "", "sun hidden when Tavinor left it out");
Check(S(d, "meeting") == "Meeting at 15:00" && S(d, "tasks") == "1 task open" && S(d, "taskList") == "", "private names");
Check(S(d, "focus") == "Focus · 12 min left" && S(d, "updated") == "Tavinor · Mon 19 Oct · 17:30", "focus, footer");

d = Data("{\"meeting\": {\"time\": \"15:00\", \"title\": \"Standup\"}, \"tasks\": {\"open\": 3, \"top\": [\"call the bank\", \"pay rent\"]}}");
Check(S(d, "meeting") == "15:00 · Standup" && S(d, "tasks") == "3 tasks open", "names shown");
Check(S(d, "taskList") == "• call the bank\n• pay rent", "task list");
Check(Data("{\"tasks\": {\"open\": 0, \"top\": []}}")["tasks"]!.ToString() == "No open tasks", "zero tasks");
Check(Data("{\"weather\": {\"error\": \"Set a city in Tavinor: Settings > Widget\"}}")["note"]!.ToString().Contains("Set a city"), "weather error");
Check(Data("{\"weather\": {\"place\": \"X\", \"temp\": 1, \"summary\": \"Fog\", \"high\": 2, \"low\": 0, \"rain\": 5, \"aqi\": null}}")["air"]!.ToString() == "", "no air quality");

// Focus counts down from `ends`, and goes away (with its Stop button) when it's over.
var at = new DateTime(2026, 10, 20, 10, 0, 0);
JsonObject Focus(DateTime now) => JsonNode.Parse(CardData.Build(
    "{\"focus\": {\"minutes_left\": 25, \"ends\": \"2026-10-20T10:25:00\", \"task\": \"\"}}", now))!.AsObject();
Check(S(Focus(at), "focus") == "Focus · 25 min left" && S(Focus(at), "focusOn") == "1", "focus from ends");
Check(S(Focus(at.AddMinutes(10).AddSeconds(30)), "focus") == "Focus · 15 min left", "focus counts down");
Check(S(Focus(at.AddMinutes(26)), "focus") == "" && S(Focus(at.AddMinutes(26)), "focusOn") == "", "focus over");
Check(S(Data("{\"focus\": {\"minutes_left\": 7, \"task\": \"\"}}"), "focus") == "Focus · 7 min left", "focus without ends");

// The large size's to-do list, and a button's error.
d = Data("{\"tasks\": {\"open\": 2, \"top\": [], \"list\": [{\"key\": \"k1\", \"text\": \"call the bank\"}, {\"key\": \"k2\", \"text\": \"pay rent\"}]}}");
Check(d["todo"]!.AsArray().Count == 2 && d["todo"]![1]!["key"]!.ToString() == "k2" && S(d, "todoAny") == "1", "to-do list");
Check(S(Data("{\"tasks\": {\"open\": 0, \"top\": [], \"list\": []}}"), "todoAny") == "" && Data(null)["todo"]!.AsArray().Count == 0, "empty to-do");
Check(JsonNode.Parse(CardData.Build("{}", null, "Tavinor is busy"))!["status"]!.ToString() == "Tavinor is busy", "status line");

// The small one-thing widgets.
d = JsonNode.Parse(CardData.Build("{" + Weather + ", \"meeting\": {\"time\": \"10:40\", \"title\": \"\"}, " +
    "\"tasks\": {\"open\": 3, \"top\": [\"call the bank\"]}, \"focus\": {\"minutes_left\": 25, \"ends\": \"2026-10-20T10:25:00\", \"task\": \"spec\"}}",
    at))!.AsObject();
Check(S(d, "aqiNum") == "191" && S(d, "aqiLabel") == "Unhealthy" && S(d, "place") == "Noida", "air widget");
Check(S(d, "rangeShort") == "H 29° · L 20°", "weather widget");
Check(S(d, "meetingTime") == "10:40" && S(d, "meetingIn") == "in 40 min" && S(d, "meetingTitle") == "Meeting", "meeting widget, private");
Check(S(d, "tasksNum") == "3" && S(d, "tasksWord") == "tasks open" && S(d, "taskFirst") == "call the bank", "tasks widget");
Check(S(d, "focusLeft") == "25" && S(d, "focusTask") == "spec", "focus widget");
string In(string time, DateTime now) => JsonNode.Parse(CardData.Build("{\"meeting\": {\"time\": \"" + time + "\", \"title\": \"x\"}}", now))!["meetingIn"]!.ToString();
Check(In("12:05", at) == "in 2 h 5 min" && In("12:00", at) == "in 2 h" && In("10:00", at) == "now", "meeting countdown");
Check(S(Data("{\"tasks\": {\"open\": 1, \"top\": []}}"), "tasksWord") == "task open" && S(Data("{\"tasks\": {\"open\": 1, \"top\": []}}"), "taskFirst") == "", "one task, names hidden");

// "Yours": Tavinor picked the rule; the card shows its lines, colour and size.
d = Data("{\"smart\": {\"line1\": \"in 8 min\", \"line2\": \"Standup\", \"color\": \"attention\", \"big\": true}}");
Check(S(d, "yours1") == "in 8 min" && S(d, "yours2") == "Standup" && S(d, "yoursNote") == "", "yours lines");
Check(S(d, "yoursColor") == "attention" && S(d, "yoursSize") == "ExtraLarge", "yours style");
d = Data("{\"smart\": null}");
Check(S(d, "yours1") == "" && S(d, "yoursNote").Contains("Settings > Widget") && S(d, "yoursColor") == "Default", "yours, no rules");

// Customize: the city now; nothing when there isn't one.
Check(JsonNode.Parse(CardData.Customize("{" + Weather + "}", "x"))!["city"]!.ToString() == "Noida", "customize city");
Check(JsonNode.Parse(CardData.Customize(null))!["city"]!.ToString() == "", "customize, no file");

// Every ${field} the cards use is in their data (else Windows shows "${field}").
// key and text belong to each to-do row (the card's $data loop).
string[] Used(string file)
{
    var card = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, file));
    JsonNode.Parse(card);
    return Regex.Matches(card, @"\$\{([^}]*)\}").SelectMany(m => Regex.Matches(Regex.Replace(m.Groups[1].Value, "'[^']*'", ""), @"(?<![\$\.\w])([a-zA-Z]\w*)")
        .Select(x => x.Groups[1].Value)).Where(w => w != "host" && w != "widgetSize").Distinct().ToArray();
}
var keys = Data(null).Select(kv => kv.Key).Concat(new[] { "key", "text" }).ToHashSet();
List<string> missing;
foreach (var file in new[] { "card.json", "weather.json", "air.json", "meeting.json", "tasks.json", "focus.json", "yours.json" })
{
    missing = Used(file).Except(keys).ToList();
    Check(missing.Count == 0, file + " fields missing from data: " + string.Join(", ", missing));
}
var cKeys = JsonNode.Parse(CardData.Customize(null))!.AsObject().Select(kv => kv.Key).ToHashSet();
missing = Used("customize.json").Except(cKeys).ToList();
Check(missing.Count == 0, "customize fields missing from data: " + string.Join(", ", missing));
Console.WriteLine($"widget card: {n} checks passed");
