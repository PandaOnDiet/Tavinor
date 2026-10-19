# Tavinor

Text-first command router with a memory. Press **Ctrl+Alt+Space**, type what you
want, press Enter.

The bar slides in from the top-right corner and fades out when it's done. Open
items and reminders arrive as toasts stacked underneath it. Click anywhere on a
toast, or press Escape, to dismiss it.

Every sentence goes through three tiers, cheapest first:

1. **Fixed shapes** (`open X`, `volume 40`, `timer 10 min`) are matched by
   regex. No model is involved, so they're instant, work offline, and can't be
   misrouted.
2. **Loose phrasing** (`skip this one`, `remind me at 5 to...`) goes to a tiny
   local model that turns it into JSON.
3. **Questions** go to a cloud model, and the answer appears in a panel.
   Copilot is one click away.

**Tasks are done in Tavinor's own chat**, by your answer provider, through Tavinor's
own commands (see [Chat](#chat)). Nothing is handed to Claude Code or any other
CLI on its own. Open items and reminders live in `notes.json` next to this file.

Touching the top edge of the screen above the bar opens it without the hotkey
([Hover to open](#hover-to-open)). The chat and answers follow your
[personality file](#personality), and [focus mode](#focus-mode) closes
distracting tabs until a session ends. The roadmap and plans live in the separate Tavinor-specs repo.

## Run

Tavinor lives in the system tray. There is no console window and no terminal left
open — it runs under `pythonw.exe`.

    python tavinor.py --install-startup   # start at login (do this once)
    python tavinor.py --remove-startup    # stop starting at login

    pythonw tavinor.py                    # start it now, windowless
    python  tavinor.py --show             # start it and open the bar immediately

Startup writes a single file to `shell:startup` (`Tavinor.vbs`). Deleting that file
disables autostart; nothing else is touched, no registry, no scheduled task.

### The bar

Ctrl+Alt+Space opens it. As you type it shows what it is about to do, so you
never press Enter blind:

| Key | Result |
|---|---|
| typing | Live rows: the action it recognised (typos corrected), matching apps, then "ask &lt;model&gt;" and "search the web" |
| ↑ / ↓ | Move the highlight |
| Enter | Runs the highlighted row, or the raw text when there are no rows |
| click / hover | Same as highlight + Enter |
| Escape | Closes the bar |

Rows come from `suggest()`, which is pure and offline — no model, no network —
because it runs on every keystroke. Anything it cannot name still goes to the
model on Enter.

**It stays open between commands.** After `timer 10 min` or a question, the bar
clears its input and waits for the next one, so one hotkey press covers a whole
run of work. It closes itself after 30 seconds idle (it is always-on-top, and a
forgotten one is in the way), on Escape, or immediately after a command that
puts another window in front — opening an app, a web search, Copilot, lock,
screenshot, media keys (`HIDE_AFTER`).

**Questions keep their thread** while the bar is open: the last two Q&A pairs
travel with the next question, so "and how do I undo that?" resolves. Closing
the bar clears it.

### Tray icon

| Action | Result |
|---|---|
| Left click | Opens the bar |
| Right click | Menu: Open Tavinor / Open items / Settings… / Quit Tavinor |
| Quit Tavinor | Removes the icon and exits the process |

### Settings

Open it three ways: the **⚙ gear** on the bar, typing **`settings`** (or
`cane settings`) in the bar, or **tray → Settings…**. `open settings` on its own
still opens *Windows* Settings, like every other `open X`. Changes apply
immediately, with no Save button. Close with ✕ or Escape.

| Switch | Default | What it does |
|---|---|---|
| Start with Windows | on | Adds/removes `Tavinor.vbs` in `shell:startup`. Reads the real file, not a saved flag |
| Cloud answers | on | On: questions go to the answer provider and the answer appears in Tavinor. Off: Tavinor sends nothing and opens Copilot with the question copied |
| Hover to open | on | Touch the top edge above the bar to open it (see [Hover to open](#hover-to-open)) |
| Claude Code button in chat | **off** | Adds *Hand to Claude Code* to the chat. Nothing goes to it unless you press it |
| Answers → Provider | Ollama | Ollama, Anthropic (Claude), OpenAI, Google Gemini, Groq, OpenRouter, NVIDIA |
| Answers → API key | — | Paste and press Save. Stored in **Windows Credential Manager** as `Cane/<provider>`, never in `settings.json`. Shown afterwards only as its last four characters. Saving an empty box deletes it |
| Answers → Model | per provider | Listed live from the provider once a key is saved (typing a name also works). Claude defaults to `claude-opus-5`; the others have no default, because their model names change too often to hard-code |
| Answers → Test | — | Sends one tiny question. Shows the reply, or the exact reason it failed |
| Features (13) | all on | Apps & sites, Volume & media, Brightness, Timers, Lock screen, Screenshot, Dark mode, Web search, Weather, Play songs, Reminders, File tasks, Focus & blocking. A switched-off feature replies "… is switched off in settings" |

Turning Reminders off stops the *commands*. Reminders you already saved still
pop up, because silently dropping one you set would be worse than a popup you
didn't expect.

### Typos

`brigtness 50`, `volum 30`, `timr 5 min`, `wether`, `settigns`, `pasue` all
work. If the exact wording matches nothing, near-miss words are snapped onto
Tavinor's command words (`VOCAB`), and the fixed-shape patterns are tried again.
The correction is **only accepted if the corrected sentence fully matches a
pattern**. Otherwise the model gets your original words. App names get the same
treatment: a close typo opens the app, a distant one gets
`no 'wtsap' - did you mean WhatsApp?`.

Real words one letter from a keyword are protected (`KEEP`). "whether to buy a
mac" isn't weather, and "the build timed out after 5 minutes" isn't a timer.
`lock` is never a correction target, because `clock` is one letter away and a
typo must never lock your screen.

The logo is a red tile with the black notch and two pixel eyes. The art lives
in the Tavinor-specs repo (`brand/`); the app carries three exports:

- `tavinor.ico` - the app icon, 16 to 256 px.
- `tavinor-tray-dark.ico`, `tavinor-tray-light.ico` - the tray icon, a white
  notch for a dark taskbar and a black one for a light taskbar, tuned pixel
  by pixel at 16 px. Tavinor picks one from the Windows theme at start.

The notch face draws the same eyes: each is a 3x5 grid of rounded cells, and
each mood switches cells on (`PIX` in `ui/app.js`), following `brand/faces/`.
Without the tray files Tavinor falls back to `tavinor.ico`, then to the stock
Windows icon.

## What it handles

| You type | What happens |
|---|---|
| `open whatsapp`, `open vs code`, `launch brave` | Any of the ~140 apps on your Start menu, typos forgiven |
| `open downloads`, `open bluetooth settings` | Folders and Settings pages |
| `open youtube`, `open gmail`, `open github.com` | Websites, or the installed web app if you have one |
| `volume 40`, `set the volume to 30%` | Sets the exact level (and unmutes) |
| `turn it up`, `quieter` | ±10%, not the 2% one media key gives |
| `brightness 70`, `brighter`, `dim the screen` | Built-in screen only; ±20 for the relative forms |
| `timer 10 min`, `set a timer for 30 seconds` | Alert with a sound when it ends |
| `lock my laptop`, `take a screenshot` | Screenshot lands in Pictures\Screenshots |
| `dark mode`, `turn off dark mode` | Switches the Windows theme |
| `google best laptops`, `search for flights to goa` | Google search in your default browser |
| `what time is it`, `weather`, `weather in goa` | Instant; weather is live from wttr.in |
| `how do I undo a git commit`, `12 times 13` | Cloud model answers in a scrollable panel with commands styled as code, and **Copy** / **Ask Copilot** |
| `ask copilot why is the sky blue` | Opens Copilot and copies the question — press Ctrl+V |
| `pause`, `skip this one`, `mute` | Media keys, works with any player |
| `play sapphire` | Resolves the top YouTube hit and **plays it** |
| `remember the auth talk is waiting on Ravi` | Saves an open item, no due date |
| `remind me in 20 minutes to check the build` | Saves it with a time; pops up when due |
| `what's left` / `what am I tracking` | Lists open items, numbered |
| `mark 2 done` / `done ravi` | Closes an item by number or by words |
| a task (`write me a script`, `set a timer and remind me after`) | Tavinor's own [chat](#chat), done by your answer provider |

### File tasks: plan, approve, undo

`clean up my downloads`, `move my invoices into paisa`, `sort my desktop` —
Tavinor shows a plan and **nothing moves until you press Do it**:

    PLAN   clean up my downloads
    8 files in Downloads
      3  Executable installers   → Downloads\Installers
      3  PDF documents           → Documents\paisa
      1  Image file              → Pictures
         1 left where they are
    nothing is deleted · Undo afterwards
                          Details   Cancel   Do it

**Details** lists every file. After Do it, **Undo** (or typing `undo`) puts the
whole batch back and removes the folders it made.

Modelled on OpenClaw's approval step (show exactly what will happen; deny by
default), but tighter where OpenClaw is weakest: its model writes shell
commands you approve as text, and its docs say an approval "is not a security
boundary". Here the planner has **no commands at all**, only
"these files → this folder":

| Rule | Why |
|---|---|
| Move only: no delete, rename, overwrite or shell | A file named `delete everything.pdf` is just a name |
| Sources must be files the planner was shown | It can't invent paths |
| Destinations only inside Downloads, Desktop, Documents, Pictures, Videos, Music (≤3 subfolders deep) | `..`, drive letters, `.ssh`, `CON` and junctions pointing outside are refused |
| Folders found through Windows (`SHGetKnownFolderPath`) | Desktop and Documents are often redirected into OneDrive |
| Hidden/system files and downloads in progress (`.crdownload`, `.part`) never offered | Moving a live download breaks it |
| Every file re-checked at Do it; changed or gone → skipped | Nothing moves on a stale plan |
| Cross-drive moves skipped, not copied | copy + delete is a delete |

The planner is your answer provider (Settings → Answers). It sees **file names,
sizes and dates** in the folder, plus the names of the folders directly inside
your six user folders, never file contents. With a cloud provider those names
leave the laptop. The last batch is recorded in `undo.json`.

Measured on a made-up fixture folder with `nemotron-3-super:cloud`: a plan in
4–5 s. "Move my invoices and bank statements into paisa" picked exactly those 3
files and found the existing `Documents\paisa`. "Clean up my downloads" sometimes
makes a folder for a single file, despite being told not to.

### Where your words go

Commands and reminders never leave the laptop: the router is a local model.
**Questions go to the answer provider you pick** in Settings — Ollama's cloud by
default, or Anthropic, OpenAI, Gemini, Groq, OpenRouter or NVIDIA with your key.
If a keyed provider fails (bad key, quota, offline), Ollama answers instead and
the panel's first line says why. Weather asks wttr.in with your IP or the place
you name. Nothing else goes out.

The Anthropic provider uses Anthropic's official SDK (`pip install anthropic`),
the only package Tavinor depends on, and only for that provider. Without it the
other six still work. Claude Opus 5 requests carry `fallbacks: "default"`: if
Claude's safety classifiers decline a question, Anthropic reruns it on a
fallback model server-side rather than returning nothing. Answers use `effort:
low`, because they are four-sentence answers in a bar.

### Why Copilot needs Ctrl+V

Neither the Copilot app (`ms-copilot:`) nor copilot.microsoft.com accepts a
question in the link. Both were tested and both opened with an empty box.
Typing it in for you would mean sending keystrokes into whichever window has
focus. If Copilot were slow to start, that could be a terminal. So Tavinor copies
the question to the clipboard and opens Copilot, and you paste it.

## Chat

`chat`, the tray's **Chat**, or any request the router reads as a task opens
Tavinor's chat window. Your answer provider does the work there through Tavinor's
own commands - apps, volume, timers, reminders, focus, file moves - in rounds:
it proposes actions, Tavinor runs them, the results go back, up to 4 rounds per
message. History is kept in `chat.json` until you press Clear.

- Every provider works, including ones without tool calling: the model replies
  with one small JSON object, and anything that isn't a known command is
  dropped before it can run.
- **Lock, undo and marking items done ask first** with an Allow / Decline card.
  Declined means nothing ran. Everything else is cheap or reversible and runs
  straight away; file moves still go through their own plan panel.
- A reply written *before* its actions ran is never shown - live, one stated
  the time before the clock had been read, and got it wrong. Only the reply
  written after the results is shown.
- A message the model never answered is dropped, so it can't be carried out
  by surprise a message later.
- **Claude Code is a button, never automatic.** With "Claude Code button in
  chat" on in Settings, the chat shows *Hand to Claude Code*; nothing goes to
  Claude Code unless you press it.

## Hover to open

Touch the top edge of the screen above the bar and it drops down. Move away
without clicking and it tucks itself back. Clicking into it keeps it open and
takes keyboard focus; until then it leaves focus where you were typing.

It never opens over a full-screen window (video, games, F11 - and the lock
screen), never while a mouse button is held (dragging a window to the top is
Windows' snap-to-maximise), and needs the cursor to rest 0.15 s (0.35 s over a
browser, whose tabs sit at the top edge). The right-most 160 px are left alone
for maximised windows' close buttons. Switch it off in Settings.

## Personality

`soul.md` holds who the assistant is (name, personality, or a preset: Concise,
Warm, Blunt, Butler, Coach), who you are, your rules, and what it has learned
about you. Edit it from **Personality…** (tray or Settings), by typing
`personality`, or in Notepad - it is read fresh for every message.

It goes to answers and the chat, **never to the command router**, which is
already at its limit. Memory is only ever *proposed* by the chat ("Remember
this?"); nothing is saved until you press Remember, and every fact can be
forgotten from the Personality page. The soul is sent to your answer provider
with each message; the switch on that page turns that off.

## Focus mode

| Say | Does |
|---|---|
| `focus` / `focus on the report for 45 min` / `focus 30` | start a session (25 min by default) |
| `how long left` · `stop focus` · `focus history` | status, stop, the last sessions |
| `block reddit` / `unblock youtube` | add or remove a site blocked during focus |
| `block youtube 9-12 on weekdays` | a schedule (daily / weekdays / weekends) |
| `what's blocked` | list both |

While a session or schedule is active, a blocked site's tab is closed with
Ctrl+W and a toast offers **Let me through 5 min**. YouTube, Twitch and X are
blocked by default. The bar shows the countdown while a session runs.

Closing a tab is the one destructive thing here, so the window must be a
browser, must still be in front, and its title must still name the site at
the moment Ctrl+W is sent; then there is a 3 s cooldown so a closing tab's
lingering title can't get the *next* tab closed. Sites are matched on whole
title parts: "Lofi - YouTube" is YouTube, "Song - YouTube Music" and "Notes
about YouTube - Google Docs" are not. A session that ends while Tavinor isn't
running is recorded as abandoned. State lives in `focus.json`.

## Tasks and habits

Tasks are the same open items as before (`what's left` lists them), with
extras that old items don't need:

| Say | Does |
|---|---|
| `task: file taxes due friday at 5pm` / `add task call mom tomorrow` | a task; the due time also pops up as a reminder |
| `subtask gather receipts to file taxes` | a subtask, listed under its task with ↳ |
| `workspace for file taxes: excel, chrome, https://incometax.gov.in` | what the task opens (⧉ in the list) |
| `start task file taxes` / `work on taxes` | opens those apps and links |
| `habit: drink water daily at 6pm` / `new habit stretch on weekdays` | a habit with a daily nudge (8pm if no time) |
| `did drink water` · `habits` · `drop habit stretch` | check in, streaks, stop (history kept) |

Task commands need `task:`, `add task` or `new task`, so `task manager` and
`open task manager` keep their old meaning. `did X` / `done X` checks in a
habit only when X names one; `done 2` still closes item 2. A streak counts
scheduled days only, so a weekday habit survives the weekend, and today
doesn't break it until the day is over. Weekdays now work in every time
phrase: `friday`, `monday at 5pm` - the next one, at 9:00 when no time is
given. Habits live in `habits.json`.

## Activity (off by default)

Settings > Activity > Track activity. Every 5 seconds the window in front is
added to a per-minute log: which app, which site (a browser's tab title, its
last part: "Lofi - YouTube" is youtube), active or idle (no input for 2
minutes). No keystrokes, screenshots or page contents. The lock screen
isn't counted. It stays in `tavinor.db`, is deleted after 30 days, and
"Delete everything" empties it.

Ask `what did I do today`, `screen time yesterday`, `when am I most
productive` (the best two hours over 14 days, not counting time on your
focus-blocked sites), `how long on youtube this week`. The morning check-in
shows yesterday in one toast after 8 AM.

## Screen access: eyes and hands

Settings > Screen access. Three levels:

| Level | Reads the window in front | Types, presses keys, clicks, switches, runs |
|---|---|---|
| Off | no | no |
| **Ask** (default) | when you ask | only after you press Allow |
| Full | yes | safe actions on its own |

**Always asks, even on Full** (stricter is fine, looser is not): running a
command, Enter (it sends messages), Delete, closing windows or tabs
(Alt+F4, Ctrl+W), typing a line break, and clicking anything named like
Send, Delete, Pay, Buy, Install, Submit, Sign in, Discard, Close. The click
check runs on the element actually found, so asking to click "Sen" and
landing on Send is refused too.

The chat sets an action's `approved` mark only after your click; the
model's own JSON can't carry it, and the screen tools refuse a gated action
without it. **Ctrl+Alt+Esc** is the panic key: access drops to Off and
typing stops at the next character. An orange bar at the top shows while it
reads or acts. Every action, refusals included, is in the Action log. Full
drops back to Ask on restart unless you keep it; Off is never raised.

Eyes use Windows UI Automation (built in): a document's text where the app
exposes it, otherwise the names of what is on screen. Hands type as Unicode
keystrokes, bring windows forward by attaching to the foreground thread (an
Alt-tap trick swallowed the first typed letter), and click through UI
Automation, or with the mouse at the element's centre when the app exposes
no click action - the cursor is put back afterwards.

## Voice

Push to talk with **Ctrl+Alt+V** or the mic on the bar; what you say runs
exactly like typed text. Windows' own speech engine finds where you start
and stop talking and keeps that audio; **Whisper** (faster-whisper, `small`
model, already on this laptop, loaded local-files-only) turns it into text.
On the same synthetic test speech Windows' dictation got 2 of 6 commands
right ("open whatsapp" -> "Open one separate"); Whisper got 6 of 6 in about
2 s each. Settings > Voice can switch back to Windows' engine.

**"Hey <name>"** is off by default. The name is the personality's, so
renaming the assistant renames the wake word. Every phrase heard while it's
on is checked by Whisper on this laptop (~2 s of CPU each; nothing is sent
anywhere), because Windows' dictation mishears "hey cane" beyond any word
filter ("I can open Croat", "Peking what time is it"). "Hey cane, open
chrome" runs "open chrome" straight away. A stand-in for the name must be
about the same length: Kane and Cain wake it, "okay cancel that" doesn't.

**Read answers aloud** speaks answers and chat replies, leaving code out,
with Piper: a natural neural voice that runs on the laptop, offline. It
needs `pip install piper-tts` and the voice file, downloaded once (60 MB):

    python -m piper.download_voices en_US-lessac-medium --download-dir voices

Tavinor loads it in the background at start (about 2 s); after that a
sentence takes ~0.1 s to make. Without Piper or the file, Windows' own
voice speaks instead. The microphone follows Screen access: at Off nothing
listens. Windows refused to start the speech scripts as `-EncodedCommand`
("Access is denied"), so their values travel in environment variables.

## The face

A small face replaces the arrow on the bar: idle (it blinks), listening,
thinking, done, error, focus, and orange eyes while screen access is in use.
Settings > Face on the bar turns it back into the arrow.

## Notes

`note: the wifi password is on the fridge`, `find note about the internet
login`, `notes`, `delete note 3`. Found by meaning: your provider's
embedding model first (Tavinor uses the first listed one that actually answers
- on this NVIDIA key 5 of 7 listed return 404), then Ollama's
`nomic-embed-text`, then plain words, and each fallback says so. Settings >
Notes > Only on this laptop skips the provider. On six notes searched with
paraphrases sharing no words, NVIDIA's model ranked the right note first 6
of 6, nomic 5 of 6. Results are the best match plus anything within 75% of
its score - models score on different scales. `note that X` (no colon)
still saves an open item, as before.

## Calendar (Google) and plan my day

Settings > Calendar has the five setup steps: you create a free OAuth
client in Google Cloud Console, paste its ID and secret, and press Sign in
with Google. Tavinor asks for calendar events only; the sign-in token is kept
in Windows Credential Manager.

`what's on my calendar tomorrow`, `add meeting design review tomorrow at
3pm for 1 hour`, `move design review to friday at 11am`, `cancel meeting
design review`. Reading and adding run at once; **moving and deleting
always ask** with a Do it / Cancel card. A toast comes 10 minutes before
each meeting.

`plan my day` fits open tasks into the free time between meetings, hardest
first and inside your best hours (from Activity) when it fits; what doesn't
fit is listed. Your answer model estimates each task's length and
difficulty (15 s cap, 30 min / medium if it can't). Tested on 200 random
days: never overlapping a meeting or another task.

## Agent hooks (MCP) for Claude Code and others

    claude mcp add tavinor -- python "D:\VS Code\Tavinor\app\mcp_server.py"

Tools: `get_day`, `search_notes` (read), `add_task`, `add_note`,
`start_focus` (each shows a Do it / Cancel card in Tavinor and waits up to 120
s; no answer is no), `animate_face`. The server forwards to the running
Tavinor over 127.0.0.1 with a fresh token per start (`bridge.json`); without
Tavinor running, tools say so. Verified with the real Claude Code: an approved
task appeared in Tavinor, a declined one didn't.

## Looking at the screen

When reading a window's text isn't enough, the chat can **look**: a
screenshot of the window in front goes to your provider's vision model
(Tavinor picks the first that answers - here `nemotron-3.5-super-vl`, which
described a test window exactly in 1.9 s). Settings > Screen access >
Screenshots to the vision model turns it off.

## Reminders

Times understood: `in 20 minutes`, `in 2 hours`, `in 3 days`, `at 3pm`,
`at 15:30`, `tomorrow`, `tomorrow at 9am`, `tonight`. A bare `at 3` means the
afternoon, and a time that has already passed today rolls to tomorrow.

If no time is understood the item is still saved — as an untimed open item
rather than being silently dropped. The bar tells you which happened.

A sweep runs every 30s and pops due reminders on top of whatever you're doing.
Each fires once. `notes.json` is plain JSON, so you can edit or clear it by hand.

## Config

| Env var | Default | Purpose |
|---|---|---|
| `CANE_MODEL` | `qwen3:1.7b` | Router: any local ollama model |
| `CANE_ANSWER_MODEL` | `nemotron-3-super:cloud` | Answers: any ollama model, local or cloud |
| `CANE_CWD` | `~` | Working directory Claude escalations start in |
| `TAVINOR_DEBUG_PORT` | (unset) | Development: start the notch with Chrome DevTools on this port |

`settings.json` holds everything the Settings window changes (`claude`,
`cloud_answers`, `provider`, `answer_model` for Ollama, `model:<provider>` for
the rest, `feature:<name>`). A missing key means the default, and a corrupt
file reads as defaults. The `answer_model` setting overrides
`CANE_ANSWER_MODEL`. API keys are not in it: see Settings.

## Test

    python test_cane.py          # logic only, no model needed
    python test_cane.py --model  # also end-to-end routing + model-only routing

`--model` runs two checks. `check_model` goes through `parse()`, which answers
most sentences from the fixed patterns. So it cannot show whether the model
understands. `check_route` calls the model directly on 46 phrasings, 17 of them
held out (none is an example in the prompt).

**Adding a command is a three-place change:** the handler in `dispatch()`, the
name in `INTENTS`, and a line plus an example in `SYSTEM`. `INTENTS` is also
the JSON schema's enum, so a command missing from it is one the model
*cannot* choose, however well it understands you. Actions with side effects
(lock, theme, screenshot, brightness, volume) also need a `MODEL_GUARDS`
entry, so a misread can never trigger them unless your words mention them.

## When a media command does nothing

    python tavinor.py --media next   # 3s to focus your player, then sends the key

This bypasses the model entirely, so it splits the two possible faults:

- **Works here, but not from the bar** - a focus problem on our side.
- **Fails here too** - your player isn't listening for media keys. Chrome and
  Edge need `Hardware Media Key Handling` enabled in `chrome://flags`; a plain
  YouTube tab ignores them otherwise.

Media keys are sent with `KEYEVENTF_EXTENDEDKEY` and the bar withdraws itself
first. Both are required: without the flag the player never sees the key, and
without the withdraw the keypress lands on our own borderless window.

## Measured

On an i5-13420H, 16 GB, no discrete GPU (CPU inference), 12 routing cases:

| Model | Median | Max | Correct |
|---|---|---|---|
| `qwen3:1.7b` | **0.77s** | 1.57s | 12/12 |
| `qwen2.5-coder:7b` | 2.49s | 7.96s | 12/12 |

Both route correctly; the 1.7b is 3.2x faster, which is the difference between
instant and noticeable.

**The model alone, 47 phrasings, patterns bypassed** (2026-09-23). "Before" is
the prompt that listed only 10 actions. Brightness, dark mode, lock, weather
and volume levels were fixed patterns the model had never been told about, and
the schema's enum made them impossible for it to choose:

| Model | Before | After | Median |
|---|---|---|---|
| `qwen3:1.7b` (local, the router) | 39/47 | **47/47** (twice) | 1.2s |
| `nemotron-3-super:cloud` | 40/47 | 47/47 | 1.0s |
| `qwen2.5-coder:7b` (local) | 39/47 | 47/47 | 3.5s, one 50s stall |

Three models of very different sizes missed the *same* phrases ("it's too
bright" → volume, "lock it" → Claude task). That pointed at the prompt, not the
model size, and fixing the prompt closed the gap without giving up the local
model. Cold load is ~12s on the 1.7b, once, then `keep_alive`
holds it in RAM.

Answer backends, same question ("undo the last commit but keep my changes"):

| Backend | Time | Answer |
|---|---|---|
| `nemotron-3-super:cloud` | **1.5–4.3s** | Correct, complete |
| `claude -p` (Haiku) | 11.0s | Correct — mostly Claude Code startup |
| `qwen3:1.7b` (local) | 4.1s | **Wrong**: `git reset --soft` with no `HEAD~1` is a no-op |
| `qwen2.5-coder:7b` (local) | 42.3s | Correct, far too slow on CPU |
| `minimax-m3:cloud` | — | Not in the free tier |

Everyday-command probe (24 sentences, real parser and dispatcher, side
effects mocked): **5 right before this tier existed, 19 now**. Of the other
five, two apps aren't installed and one folder doesn't exist, and Tavinor says so.
One is a task that correctly waits for Claude Code. The last is `shut down in 1
hour`, which isn't built yet.

## Notes

- **Tavinor starts ollama itself** if it is not running, and preloads the model at
  launch so the first thing you type is fast. If the server genuinely cannot be
  started, commands report `model offline` — they no longer fall back to
  escalation. Escalating on a dead dependency turned every keystroke into a new
  terminal window while making the app look like it was working.
- `keep_alive: 8h` keeps the model resident once loaded.
- `play <song>` resolves the top YouTube result's video id from the search HTML
  and opens the watch URL, so the song actually starts. Opening a search page
  was never "play the song" — it showed results and waited for a click. Costs
  ~2s for the search fetch. Falls back to the search page if no id is found.
- `think: false` matters — qwen3 is a hybrid reasoner, and thinking blows the
  latency budget for what is only a classification job.
- Time parsing is a regex, deliberately not the model. Small models fumble date
  arithmetic, and a reminder at the wrong time is worse than no reminder.
- The model also loads **each time the bar opens**, using an empty generate
  that costs no inference. After 8h idle the model was unloaded and the first
  command took 19s. Loading while you type hides most of that.
- Apps come from `Get-StartApps`, read once at launch (~1–5s, in the
  background). PATH, which Tavinor used before, held a handful of command-line
  tools. WhatsApp, Settings and Brave were installed but couldn't be opened.
- Volume goes through the endpoint-volume COM interface via ctypes. Media keys
  only step 2% and can't set a level.
- Adding an intent means adding an example to `SYSTEM` too. Dropping the media
  examples once cost `skip this one`, which silently started escalating instead.

## Hardening

Each of these was a real defect, found by probing rather than by the happy-path
tests. `check_edges()` in `test_cane.py` guards all of them.

- **No shell anywhere.** Escalation runs `claude` directly with
  `CREATE_NEW_CONSOLE`; launching uses `os.startfile` on a resolved path. Going
  through `cmd /c start ... cmd /k` meant cmd reparsed the sentence, so
  `x" & calc & "y` actually launched calc — and any ordinary command containing
  `&`, `|`, `>` or a quote was silently mangled.
- **An empty `done` ref matches nothing.** Substring-matching on `""` is true for
  every row, so a bare "done" quietly closed whichever item was first.
- **Nonsense times save an untimed item** instead of raising. `at 25pm` used to
  crash out of dispatch.
- **A corrupt `notes.json` degrades to empty.** Every row is normalised on load,
  so a hand edit or a truncated write cannot take the app down.
- **One bad date no longer stops every reminder.** The sweep skips the row and
  marks it, rather than aborting the whole pass.
- Writes take a lock: the reminder sweep and the bar are different threads.
- **Toasts dismiss on click, not just Escape.** A borderless Toplevel may never
  take keyboard focus, which left them impossible to close.
- **Only one list window exists at a time.** Repeated "what's left" used to
  stack new windows downward until they ran off the bottom of the screen.
- **A media intent needs a media word.** A small model forced to choose picks
  the nearest verb: `turn on dark mode` pressed volume-down.
- **A reminder about a command is not the command.** `remind me to take a
  screenshot at 5` must save a note; the fixed-shape tier steps aside for any
  sentence starting with remind/remember/note.
- **Weather never reaches a language model.** It has no live data and would
  guess, so weather is matched first and fetched live.
- **"Settings" opens Settings.** Two Start-menu apps are named Settings, one a
  notification tool. Settings pages are resolved before the app index.
- **A misspelt keyword can't pick a wrong action.** `brigtness up` once reached
  the model and turned the *volume* up. The word "up" satisfied the media guard.
  Typo correction now runs before the model, and only a full pattern match
  counts.
- **Setting descriptions anchor top-left.** A west-anchored wrapped line is
  centred on its y, so a two-line description grew upward over its own label.
- **Panels stack below the bar's *current* height.** With a result list open
  the bar is up to 210px tall, and a fixed 104 would have dropped an alert on
  top of the list (`_bar_h()`).
- **A kept-open bar never takes focus back.** `status()` deliberately does not
  `focus_force()`: if you moved to another window while the command ran,
  yanking focus back would interrupt you.

## UI notes

- Rounded corners are faked: the window is keyed out with `-transparentcolor`
  and the panel is a smoothed polygon on a Canvas. Tk has no rounded primitive.
  If a display refuses the keying, `make_panel` falls back to square corners
  rather than failing.
- `unfocus()` withdraws with no animation, deliberately. The media keypress is
  sent 250ms later and an animating window would still own focus when it lands.
- Layout is anchored to the right edge, so it lands correctly on any width.
