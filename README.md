<div align="center">

<img src="static/logo.svg" alt="Market Forge" width="340">

### Your AI trading desk.

Catalyst radar, hard-coded discipline, and a copilot that builds your board in real time.<br>
**Open source · runs on your machine · your keys never leave it · paper by default**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Discord](https://img.shields.io/badge/discord-join-5865F2?logo=discord&logoColor=white)](https://discord.gg/JE8TEYZp2f)

[Website](https://madeformeai.com/marketforge) · [Docs](https://docs.madeformeai.com/marketforge/index) · [Discord](https://discord.gg/JE8TEYZp2f) · [Agent install](AGENT-INSTALL.md) · [Dependencies](DEPENDENCIES.md)

<img src="docs/screenshots/demo.gif" alt="Market Forge in motion" width="850">

<sub><a href="https://youtu.be/Q2wnAheNqRw">▶ Watch the full 100-second narrated demo</a></sub>

</div>

---

## What is this

Market Forge is a **local trading desk with an AI copilot in the seat next to you**.
A scanner engine finds catalyst-driven movers, verifies every number against the
live tape, scores them signal-vs-noise, and enforces hard risk gates. A dashboard
renders it GridPulse-style. And a coding agent (Claude Code, Codex, any of them)
drives the desk through plain files: you talk - by voice or chat - and it builds
boards, replays your trading day, and honors your standing rules on every turn.

It was built by a network engineer learning swing trading who wanted his
discipline **in code, not in willpower**.

## What you need

**Required:** Python 3.10+ · git · Flask (one `pip install`) · a free
[Alpaca](https://alpaca.markets) account (paper keys to start).

**Optional, each unlocking one thing:** an AI coding agent (the copilot) ·
[Ollama](https://ollama.com) (catalyst scoring) · [Voicebox](https://voicebox.sh/) (a natural voice) ·
Chrome/Edge (voice input) · a Discord webhook (phone alerts).

No Docker, no database, no Node, no cloud account. Details: [DEPENDENCIES.md](DEPENDENCIES.md).

## Install

**One-liner (Windows PowerShell):**
```powershell
powershell -c "irm https://madeformeai.com/marketforge/install.ps1 | iex"
```

**One-liner (macOS / Linux):**
```bash
curl -fsSL https://madeformeai.com/marketforge/install.sh | bash
```

**Have your AI agent do it:** say this to Claude Code, Codex, Cursor, any of them.

```
Claude, set up Market Forge on my machine:
https://madeformeai.com/marketforge/setup.md
```

It reads the rest itself, then installs, runs the key wizard *with* you, and learns
your plan. Details: [AGENT-INSTALL.md](AGENT-INSTALL.md).

**Manual:** clone this repo, `pip install -r requirements.txt`, copy
`bot/.env.template` to `bot/.env` with your [Alpaca](https://alpaca.markets) paper
keys, then `run-portable.bat` (Windows) or `MF_EMBEDDED=1 python app.py`.
Full walkthrough: [PORTABLE.md](PORTABLE.md).

> Scripts fallback while the site propagates:
> `irm https://raw.githubusercontent.com/almnjoy/MarketForge/main/install.ps1 | iex`

## The desk

| | |
|---|---|
| **Catalyst radar** | Alpaca movers screener, every % re-verified against real bars and the live tape (stale prints and split artifacts get dropped), LLM triage with news + Reddit hot-page buzz folded in, live "now vs alert" on every card. |
| **Hard-coded discipline** | Fail-closed gates: conviction score floor, price floor, per-trade size, entries/day, total exposure, kill-switch. Every auto-entry exits via a GTC **trailing stop armed at fill** - no naked positions, ever. |
| **The copilot** | Chat or talk (hot-mic, spoken replies). It builds live panels on the Workbench while you watch, honors your `memory.md` standing orders, explains any knob on the RULES tab, and replays your day from the journal - git history for your decisions. |

<div align="center">
<img src="docs/screenshots/overview.png" alt="Overview - account, chart and open positions" width="850">
<sub>The desk on open: real account, live chart, open positions, and the PAPER badge that ships by default.</sub>

<img src="docs/screenshots/retail-radar.png" alt="Retail radar - Reddit buzz folded into the score" width="850">
<sub>Retail radar - what r/wallstreetbets, r/swingtrading and r/stocks are actually talking about, ranked by heat.</sub>

<img src="docs/screenshots/workbench.png" alt="Workbench - panels the copilot builds live" width="850">
<sub>The Workbench. Ask for a board and the copilot writes it while you watch.</sub>

<img src="docs/screenshots/rules.png" alt="Rules - the gates the engine actually runs" width="850">
<sub>Every gate, in plain English, exactly as the engine is running it.</sub>
</div>

More of the cockpit: `Ctrl+K` command palette (anything it doesn't recognize goes
to the copilot), saved boards, drag-resizable panels, market-session clock,
activity dock with the agent's real tool steps, and a trade ticket where the
trailing stop is filled in before you ever type "live".

## How it works

In plain English: it watches the market for you, throws out the fake moves,
rates what's left, checks it against **your** rules, and never lets a position
sit without an exit.

```mermaid
flowchart TD
    You(["You"]) <-->|"talk or type"| Desk["Market Forge<br/><small>runs on your computer</small>"]
    Desk <-->|"asks it things, it builds you boards"| Copilot(["Your AI copilot"])

    Desk --> Scan["Checks what's moving<br/><small>4 times a day</small>"]
    Scan --> Real{"Is that move real?<br/><small>checks the live price itself</small>"}
    Real -->|"no — stale or fake number"| Drop["Thrown out<br/><small>you never see it</small>"]
    Real -->|"yes"| Rate["Reads the news and Reddit,<br/>rates it 0-100"]
    Rate --> Rules{"Does it pass YOUR rules?<br/><small>score, price floor, daily limits</small>"}
    Rules -->|"no"| Skip["Listed as noise<br/><small>nothing happens</small>"]
    Rules -->|"yes"| Ready["On your radar<br/><small>with the reason why</small>"]

    Ready --> Who{"Who pulls the trigger?"}
    Who -->|"you — this is the default"| Manual["You click Trade"]
    Who -->|"only if you switch it on"| Auto["It buys a small amount"]
    Manual --> Exit["An automatic sell-stop is armed<br/><small>it follows the price up, never down</small>"]
    Auto --> Exit
    Exit --> Journal["Written to your journal:<br/>what happened and why"]
    Skip --> Journal
    Journal -->|"replay yesterday with me"| Copilot

    classDef you fill:#4f9dff,stroke:#4f9dff,color:#04101f
    classDef ai fill:#ff6a00,stroke:#ff6a00,color:#160a00
    classDef step fill:#182131,stroke:#3d5372,color:#e6edf6
    classDef gate fill:#2a1520,stroke:#f05252,color:#ffd7d7
    classDef good fill:#0f2a1f,stroke:#34d399,color:#c9ffe9
    classDef dead fill:#181d26,stroke:#4a5568,color:#93a3b8
    class You you
    class Copilot ai
    class Desk,Scan,Rate,Ready,Manual,Auto,Journal step
    class Real,Rules,Who gate
    class Exit good
    class Drop,Skip dead
```

**The part that matters:** nothing trades itself unless you deliberately turn
that on, and *every* entry - yours or its - gets an exit armed the moment it
fills. See [rules and risk](https://docs.madeformeai.com/marketforge/rules-and-risk).

<details>
<summary>Under the hood (the technical version)</summary>

<img src="docs/architecture.svg" alt="Market Forge architecture" width="100%">

Two run modes: `run-portable.bat` = everything in one window (the one you want).
`run.bat` = dashboard only, engine hosted elsewhere.

</details>

## Safety, plainly

Ships in **paper mode** with **auto-trading double-locked off**. Going live is a
deliberate, documented act ([PORTABLE.md](PORTABLE.md)) and starts hard-capped.
The copilot is contractually forbidden (CLAUDE.md) from placing trades or
touching risk settings without your explicit instruction. Market Forge is a
research and education tool - not financial advice; trading involves substantial
risk of loss.

## Community

Questions, setups, and boards worth stealing: [Discord](https://discord.gg/JE8TEYZp2f).
Built by [@almnjoy](https://github.com/almnjoy) · part of the
[MadeForMeAI](https://madeformeai.com) family.

## License

[MIT](LICENSE)
