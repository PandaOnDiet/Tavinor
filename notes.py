"""Notes you can find by meaning (phase 9).

  note: Ravi's wifi password is on the fridge
  find note where is the internet login        -> finds it, no shared words
  notes                                        -> the latest ones
  delete note 3                                -> by its number in that list

Separate from open items (those are things to do; these are things to
know). Stored in notebook.json beside cane.py.

Meaning comes from an embedding model. Settings > Notes picks the source:
  provider (default)  your answer provider's embedding model (NVIDIA: the
                      first listed model that actually answers - most listed
                      ones return 404 for this key), falling back to...
  ollama              nomic-embed-text, already installed; nothing leaves
                      the laptop.
If neither answers, search falls back to plain words and says so. Vectors
are kept per model, so switching source never mixes incompatible numbers;
a note missing a vector for the current model gets one on the next search.
"""
import json
import math
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import cane

BOOK = Path(__file__).with_name("notebook.json")
OLLAMA_EMBED = "nomic-embed-text"
PREFER = ("nemotron-3-embed", "embed")           # NVIDIA: try these first
# Models score on different scales (NVIDIA's right answers: 0.14-0.55;
# nomic's: 0.55-0.72), so a fixed cutoff hid half of NVIDIA's correct
# results. Keep the best, and anything close to it.
NEAR_BEST = 0.75
_LOCK = threading.Lock()


# ------------------------------------------------------------- store

def load():
    try:
        rows = json.loads(BOOK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and str(r.get("text", "")).strip():
            vec = r.get("vec") if isinstance(r.get("vec"), dict) else {}
            out.append({"id": str(r.get("id") or uuid.uuid4().hex[:8]),
                        "text": str(r["text"]).strip(),
                        "created": str(r.get("created", "")),
                        "vec": {k: v for k, v in vec.items() if isinstance(v, list)}})
    return out


def save(rows):
    BOOK.write_text(json.dumps(rows, indent=1), encoding="utf-8")


def add(text, now=None):
    text = " ".join(text.split())[:2000]
    row = {"id": uuid.uuid4().hex[:8], "text": text, "vec": {},
           "created": (now or datetime.now()).isoformat(timespec="minutes")}
    with _LOCK:
        rows = load()
        rows.append(row)
        save(rows)
    threading.Thread(target=_embed_missing, daemon=True).start()  # ready for search
    return row


def recent(n=10):
    return list(reversed(load()))[:n]


def delete(number):
    """By its 1-based number in the newest-first list."""
    with _LOCK:
        rows = load()
        newest = list(reversed(rows))
        if not 1 <= number <= len(newest):
            return None
        gone = newest[number - 1]
        save([r for r in rows if r["id"] != gone["id"]])
        return gone


# ------------------------------------------------------------- embeddings

def source():
    return "ollama" if cane.setting("embed", "provider") == "ollama" else "provider"


def _post(url, payload, headers=None, timeout=30):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _ollama(texts, kind):
    # nomic-embed-text is trained with these prefixes; without them search
    # quality drops noticeably.
    pre = "search_query: " if kind == "query" else "search_document: "
    d = _post(cane.OLLAMA.replace("/api/chat", "/api/embed"),
              {"model": OLLAMA_EMBED, "input": [pre + t for t in texts]}, timeout=90)
    return d["embeddings"]


def _provider_model(prov):
    """The provider's embedding model: saved, or the first listed that works."""
    saved = cane.setting("embed_model:" + prov)
    if saved:
        return saved
    names = [m for m in cane.provider_models(prov) if "embed" in m.lower()]
    names.sort(key=lambda m: next((i for i, p in enumerate(PREFER) if p in m), 99))
    for m in names[:8]:
        try:
            _provider(prov, m, ["probe"], "query")
            cane.set_setting("embed_model:" + prov, m)
            return m
        except Exception:
            continue
    return None


def _provider(prov, model, texts, kind):
    base = cane.PROVIDERS[prov][1]
    payload = {"model": model, "input": texts, "encoding_format": "float"}
    if prov == "nvidia":
        payload["input_type"] = kind                     # NVIDIA requires it
    d = _post(base + "/embeddings", payload,
              {"Authorization": "Bearer " + (cane.key_get(prov) or "")})
    return [row["embedding"] for row in sorted(d["data"], key=lambda r: r["index"])]


def embed(texts, kind="passage"):
    """(model_key, vectors, note). Provider first unless set to Ollama; then
    Ollama; (None, None, note) when nothing answers."""
    tried = []
    if source() == "provider":
        prov = cane.provider()
        if prov not in ("ollama", "anthropic") and cane.key_get(prov):
            try:
                model = _provider_model(prov)
                if model:
                    return "%s:%s" % (prov, model), _provider(prov, model, texts, kind), ""
                tried.append("%s has no working embedding model" % prov)
            except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
                tried.append("%s embeddings failed (%s)" % (prov, str(e)[:40]))
        else:
            tried.append("%s has no embeddings" % cane.PROVIDERS[prov][0])
    try:
        return "ollama:" + OLLAMA_EMBED, _ollama(texts, kind), "; ".join(tried)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        tried.append("ollama failed (%s)" % str(e)[:40])
    return None, None, "; ".join(tried)


def _embed_missing():
    """Give every note a vector for the model in use now."""
    rows = load()
    probe_key, _, _ = embed(["probe"], "query")
    if not probe_key:
        return None
    todo = [r for r in rows if probe_key not in r["vec"]]
    for i in range(0, len(todo), 32):
        chunk = todo[i:i + 32]
        key, vecs, _ = embed([r["text"] for r in chunk], "passage")
        if key != probe_key or not vecs:
            return probe_key
        with _LOCK:
            fresh = load()
            by_id = {r["id"]: v for r, v in zip(chunk, vecs)}
            for r in fresh:
                if r["id"] in by_id:
                    r["vec"][key] = [round(x, 6) for x in by_id[r["id"]]]
            save(fresh)
    return probe_key


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(query, n=5):
    """([(score, note)], note_about_how). Best first."""
    _embed_missing()
    key, vecs, why = embed([query], "query")
    rows = load()
    if key:
        q = vecs[0]
        scored = [(cosine(q, r["vec"][key]), r) for r in rows if key in r["vec"]]
        scored.sort(key=lambda s: -s[0])
        if scored:
            scored = [s for s in scored if s[0] >= scored[0][0] * NEAR_BEST
                      and s[0] > 0.05]
        return scored[:n], ("" if not why else why + " - used " + key)
    words = set(re.findall(r"\w{3,}", query.lower()))
    scored = [(len(words & set(re.findall(r"\w{3,}", r["text"].lower()))) / max(1, len(words)), r)
              for r in rows]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: -s[0])
    return scored[:n], "matched by words only - %s" % why


# ------------------------------------------------------------- commands

def quick(t):
    m = re.fullmatch(r"(?:note|jot)(?: down)?:\s*(?P<text>.+)|(?:jot|write) down (?P<t2>.+)"
                     r"|(?:take|make) a note:? (?P<t3>.+)", t)
    if m:
        return {"intent": "note", "action": "add",
                "arg": m["text"] or m["t2"] or m["t3"]}
    m = re.fullmatch(r"(?:find|search|look up|look for) (?:my )?notes? (?:about |for |on )?"
                     r"(?P<q>.+)|what did i (?:note|write down) about (?P<q2>.+)", t)
    if m:
        return {"intent": "note", "action": "search", "arg": m["q"] or m["q2"]}
    if re.fullmatch(r"(?:show |list )?(?:my )?notes|(?:my )?notebook", t):
        return {"intent": "note", "action": "list"}
    m = re.fullmatch(r"(?:delete|remove) note (?P<n>\d+)", t)
    if m:
        return {"intent": "note", "action": "delete", "arg": m["n"]}
    return None


def dispatch(intent, ui=None):
    act, arg = intent.get("action"), str(intent.get("arg") or "").strip()
    if act == "add":
        if not arg:
            return "what should the note say?"
        add(arg)
        return "noted: " + arg[:50]
    if act == "list":
        rows = recent(15)
        if not rows:
            return "no notes yet - try: note: the wifi password is on the fridge"
        if ui:
            ui.show_answer("notes", "\n".join("%2d  %s" % (i, r["text"][:90])
                                              for i, r in enumerate(rows, 1)))
        return "%d note%s" % (len(load()), "" if len(load()) == 1 else "s")
    if act == "search":
        hits, how = search(arg)
        if not hits:
            return "no note matches %r%s" % (arg[:30], (" (" + how + ")") if how else "")
        text = "\n".join("%3d%%  %s" % (round(s * 100), r["text"][:140]) for s, r in hits)
        if ui:
            ui.show_answer("notes about " + arg[:30], (how + "\n" if how else "") + text)
        return "best match: " + hits[0][1]["text"][:200]
    if act == "delete":
        gone = delete(int(arg)) if arg.isdigit() else None
        return ("deleted: " + gone["text"][:40]) if gone else "no note %s" % arg
    return "unknown note action"


try:
    import agent
    agent.EXTRA["note"] = (
        '{"intent":"note","action":"add","arg":"<text>"}  save a note; "action":'
        '"search","arg":"<what to look for>" finds notes by meaning; "action":"list"',
        lambda a: a.get("action") != "delete")
except ImportError:
    pass
