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

// Every ${field} the card uses is in the data (else Windows shows "${field}").
var card = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "card.json"));
JsonNode.Parse(card);
var keys = Data(null).Select(kv => kv.Key).ToHashSet();
var used = Regex.Matches(card, @"\$\{([^}]*)\}").SelectMany(m => Regex.Matches(Regex.Replace(m.Groups[1].Value, "'[^']*'", ""), @"(?<![\$\.\w])([a-zA-Z]\w*)")
    .Select(x => x.Groups[1].Value)).Where(w => w != "host" && w != "widgetSize").ToHashSet();
var missing = used.Except(keys).ToList();
Check(missing.Count == 0, "card fields missing from data: " + string.Join(", ", missing));
Console.WriteLine($"widget card: {n} checks passed");
