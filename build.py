"""Market Forge - Windows desktop build.

    python build.py            -> dist/MarketForge/  +  dist/MarketForge-win64.zip

What it does, in order:
  1. icon: demo/logo-on-dark.png -> build/MarketForge.ico (Pillow)
  2. PyInstaller one-folder build from MarketForge.spec (exe + _internal/)
  3. copies the SIBLING FILES the app runs from: static/, bot/ (code +
     .env.template ONLY), panels/00-welcome.html, RULES.md, CLAUDE.md, and a
     clean default config.json
  4. refuses to ship secrets or personal state (bot/.env, bot/data, chat logs,
     memory.md) and verifies that before zipping

The result is the whole product: a folder a friend unzips and double-clicks.
First launch has no bot/.env, so the desk serves the setup wizard.

Build deps (dev machine only): pip install pyinstaller pillow pywebview
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "MarketForge"
NEVER_SHIP = ["bot/.env", "bot/data", "chat-inbox.jsonl", "chat-outbox.jsonl",
              "memory.md", "journal.jsonl", "usage.jsonl", "state.json",
              "webview-data", "logs", "tv-shots", "saved-workbenches"]

# A fresh default config - NOT the repo one, which carries personal picks
# (Dustin's cloned voice, opus bridge). sonnet is the friend default.
DEFAULT_CONFIG = """{
  "port": 8410,
  "voicebox_url": "http://127.0.0.1:17493",
  "bridge_model": "sonnet",
  "bridge_timeout": 150,
  "splash_ms": 2500,
  "embedded": true
}
"""


def step(msg):
    print(f"\n=== {msg}")


def make_icon() -> Path:
    from PIL import Image
    src = ROOT / "demo" / "logo-on-dark.png"
    out = ROOT / "build" / "MarketForge.ico"
    out.parent.mkdir(exist_ok=True)
    img = Image.open(src).convert("RGBA")
    # square-pad so the mark is not squashed into 1:1
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    canvas.save(out, sizes=[(s, s) for s in (256, 128, 64, 48, 32, 16)])
    print(f"icon: {out}")
    return out


def pyinstaller():
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        "--clean", str(ROOT / "MarketForge.spec")], cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"PyInstaller failed rc={r.returncode}")
    if not (DIST / "MarketForge.exe").exists():
        raise SystemExit("PyInstaller finished but MarketForge.exe is missing")


def copy_siblings():
    def cp_tree(rel, dst_rel=None, exclude=()):
        src, dst = ROOT / rel, DIST / (dst_rel or rel)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*exclude),
                        dirs_exist_ok=True)
        print(f"  + {rel}/")

    cp_tree("static")
    cp_tree("bot", exclude=(".env", "data", "__pycache__", "*.pyc",
                            ".env.bak*", ".env.hidden"))
    (DIST / "panels").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "panels" / "00-welcome.html", DIST / "panels")
    print("  + panels/00-welcome.html")
    for f in ("RULES.md", "CLAUDE.md"):
        shutil.copy2(ROOT / f, DIST / f)
        print(f"  + {f}")
    (DIST / "config.json").write_text(DEFAULT_CONFIG, encoding="utf-8")
    print("  + config.json (clean defaults)")


def verify():
    bad = [p for p in NEVER_SHIP if (DIST / p).exists()]
    if bad:
        raise SystemExit(f"REFUSING TO SHIP - secrets/personal state in dist: {bad}")
    for must in ("MarketForge.exe", "static/index.html", "static/setup.html",
                 "bot/run_bot.py", "bot/src/api.py", "bot/.env.template",
                 "panels/00-welcome.html", "RULES.md", "CLAUDE.md"):
        if not (DIST / must).exists():
            raise SystemExit(f"dist is missing {must}")
    print("verified: no secrets, all required files present")


def zip_dist() -> Path:
    out = ROOT / "dist" / "MarketForge-win64.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in DIST.rglob("*"):
            if f.is_file():
                z.write(f, Path("MarketForge") / f.relative_to(DIST))
    mb = out.stat().st_size / 1e6
    print(f"zip: {out} ({mb:.1f} MB)")
    return out


def main():
    step("icon")
    make_icon()
    step("pyinstaller (one-folder)")
    pyinstaller()
    step("sibling files")
    copy_siblings()
    step("verify")
    verify()
    step("zip")
    zip_dist()
    step(f"done -> {DIST}")


if __name__ == "__main__":
    main()
