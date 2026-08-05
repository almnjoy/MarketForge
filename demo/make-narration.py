"""Generate the demo narration in YOUR Voicebox voice - one WAV per scene.

Reads demo/script.md, sends each numbered scene's narration line to Voicebox
(Kokoro), and writes demo/audio/NN-slug.wav. Drop those into OBS / your editor
on an audio track and record the screen against them.

Usage (Market Forge folder, Voicebox running):
    python demo/make-narration.py
    python demo/make-narration.py --scene 3      # redo one scene
    python demo/make-narration.py --list         # show scenes, generate nothing
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "script.md"
OUT = HERE / "audio"

CFG = {}
try:
    CFG = json.loads((HERE.parent / "config.json").read_text(encoding="utf-8"))
except Exception:
    pass
VOICEBOX = str(CFG.get("voicebox_url", "http://127.0.0.1:17493")).rstrip("/")


def scenes():
    """Parse '## NN. Title' headings + their '> narration' lines from script.md."""
    if not SCRIPT.exists():
        sys.exit(f"missing {SCRIPT}")
    out, cur = [], None
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        h = re.match(r"^##\s+(\d+)\.\s+(.+?)\s*$", line)
        if h:
            cur = {"n": int(h.group(1)), "title": h.group(2), "text": []}
            out.append(cur)
        elif cur is not None and line.startswith(">"):
            t = line.lstrip("> ").strip()
            if t:
                cur["text"].append(t)
    for s in out:
        s["text"] = " ".join(s["text"])
    return [s for s in out if s["text"]]


def profile_id():
    req = urllib.request.Request(f"{VOICEBOX}/profiles", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    items = data if isinstance(data, list) else data.get("profiles", [])
    for p in items:
        if (p.get("preset_engine") or p.get("default_engine") or "").lower() == "kokoro":
            return p.get("id") or p.get("profile_id")
    sys.exit("No Kokoro voice profile found in Voicebox. Create one, then rerun.")


def say(text, pid, dest):
    body = json.dumps({"text": text, "profile_id": pid,
                       "engine": "kokoro", "language": "en"}).encode()
    req = urllib.request.Request(f"{VOICEBOX}/generate/stream", data=body,
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        audio = r.read()
    dest.write_bytes(audio)
    return len(audio)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", type=int, help="only this scene number")
    ap.add_argument("--list", action="store_true", help="list scenes and exit")
    a = ap.parse_args()

    sc = scenes()
    if a.list:
        for s in sc:
            print(f"{s['n']:>2}. {s['title']}  ({len(s['text'].split())} words)")
        return

    OUT.mkdir(exist_ok=True)
    pid = profile_id()
    print(f"Voicebox {VOICEBOX} · profile {pid}\n")
    for s in sc:
        if a.scene and s["n"] != a.scene:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", s["title"].lower()).strip("-")[:40]
        dest = OUT / f"{s['n']:02d}-{slug}.wav"
        print(f"  {s['n']:>2}. {s['title']} ...", end=" ", flush=True)
        try:
            n = say(s["text"], pid, dest)
            print(f"{n // 1024} KB -> {dest.name}")
        except Exception as e:
            print(f"FAILED: {str(e)[:110]}")
    print(f"\nDone. Audio in {OUT}")
    print("Import into OBS/your editor as an audio track, then record the screen to it.")


if __name__ == "__main__":
    main()
