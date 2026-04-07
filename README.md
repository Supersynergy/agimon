<p align="center">
  <img src="https://img.shields.io/badge/⚡_AGIMON-Gotta_monitor_'em_all-ff9500?style=for-the-badge&labelColor=1a1a1a" alt="AGIMON" />
</p>

<h1 align="center">⚡ AGIMON</h1>
<h3 align="center">Gotta monitor 'em all.</h3>

<p align="center">
  <strong>The AGI-grade monitor for AI coding agents.</strong><br/>
  One command to see every Claude Code, Gemini CLI, Codex instance — what it's doing, what it costs, and kill it if needed.
</p>

<p align="center">
  <a href="#-30-second-setup"><img src="https://img.shields.io/badge/Setup-30_seconds-00d084?style=flat-square" /></a>
  <a href="#-what-you-get"><img src="https://img.shields.io/badge/Features-29-ff9500?style=flat-square" /></a>
  <img src="https://img.shields.io/badge/macOS-Ghostty_Native-333?style=flat-square&logo=apple" />
  <img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776ab?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Qdrant-Semantic_Memory-dc382d?style=flat-square" />
  <img src="https://img.shields.io/badge/Ghostty-AppleScript_API-ff9500?style=flat-square" />
  <img src="https://img.shields.io/badge/Raycast-Extension-ff6363?style=flat-square" />
  <img src="https://img.shields.io/badge/SketchyBar-Desktop_Widget-7c3aed?style=flat-square" />
</p>

---

## The Problem

You're running **10+ AI coding agents** across terminals. Each one spawning subagents, burning tokens, making network calls. You have **zero visibility**.

- Which instance is doing what? 🤷
- How much am I spending right now? 💸
- What's eating my CPU? 🔥
- Which SSH tunnel goes where? 🕳️
- What did that agent do 3 hours ago? 🧠

**AGIMON catches 'em all.**

---

## 🎬 What You Get

```
$ agimon

╔═══════════════════════════════════════╗
║  ⚡ AGIMON v1.0.0 — Gotta monitor 'em all    ║
╚═══════════════════════════════════════╝

⚡ Claude Code: 5 aktiv  17 idle  22 total
  CPU: 38.7%  RAM: 10213MB

💻 claude (22)  CPU:38.7%  RAM:10213MB
  ● Claude Code ████████░░  11.3%    596MB  PID:88831  11:27AM
  ● Claude Code ███░░░░░░░   5.5%    554MB  PID:74558  11:20AM
  ○ Claude Code ░░░░░░░░░░   0.0%    688MB  PID:49964   3:14AM
  ...

🔧 dev-tool (3)  CPU:24.4%  RAM:654MB
  ● HTTPX Scanner ████████░░  23.8%    421MB  PID:62049
  ● Ollama LLM    ██████░░░░  16.0%    460MB  PID:1528

📝 ide (6)  CPU:1.6%  RAM:856MB
  ○ Windsurf IDE  ░░░░░░░░░░   1.1%    480MB  PID:94579

💰 Kosten (14 Tage)
  $3,196.40  1.6B tok  3604 sess  19957 msgs

  2026-04-05 ████████░░░░░░░░░░░░  $131.26  223s
  2026-04-04 ██░░░░░░░░░░░░░░░░░░   $28.39  526s
  2026-04-03 ███░░░░░░░░░░░░░░░░░   $37.39  623s
```

---

## ⚡ 30-Second Setup

```bash
git clone https://github.com/supersynergy/agimon.git
cd agimon
uv venv .venv && source .venv/bin/activate
uv pip install textual psutil rumps qdrant-client
ln -sf $(pwd)/agimon ~/.local/bin/agimon
```

**Done.** Type `agimon` and see everything.

---

## 🎯 Commands

### Monitoring

| Command | What |
|---------|------|
| `agimon` | Full dashboard — processes + costs |
| `agimon live` | All running AI agents with live CPU/RAM bars |
| `agimon sessions` | Active sessions + subagent tree + tool usage |
| `agimon costs` | 14-day cost breakdown with sparkline bars |
| `agimon net` | SSH tunnels, services, external connections |
| `agimon ghostty` | Every Ghostty terminal with working dirs |
| `agimon windows` | All visible windows across all macOS apps |

### Orchestration

| Command | What |
|---------|------|
| `agimon delegate "task"` | Auto-route to cheapest model that can handle it |
| `agimon snap` | Full system snapshot (JSON) |
| `agimon send 1 2 "ls"` | Send command to any Ghostty terminal |
| `agimon focus 3` | Focus any window (even across Spaces) |
| `agimon open ~/project` | One-click: Ghostty + Claude in that dir |

### Memory (Qdrant)

| Command | What |
|---------|------|
| `agimon search "auth refactor"` | Semantic search over all past sessions |
| `agimon index` | Auto-index current sessions into Qdrant |
| `agimon history "query"` | Find what worked before |

### Launch

| Command | What |
|---------|------|
| `agimon tui` | Interactive 7-tab TUI dashboard |
| `agimon menubar` | Persistent macOS menubar app |
| `agimon qdrant` | Open Qdrant dashboard |
| `agimon kill <pid>` | Kill a process |
| `agimon kill-all` | Stop all Claude instances |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────┐
│           Layer 4: CLI  ─  agimon           │
│         One command for everything           │
├─────────────────────────────────────────────┤
│        Layer 3: macOS Menubar (rumps)        │
│   Process control · Project launch ·         │
│   Kill/Info/Focus · Favorite projects        │
├─────────────────────────────────────────────┤
│       Layer 2: TUI Dashboard (Textual)       │
│   7 tabs: Live · Sessions · Stats ·          │
│   Network · Ghostty · Windows · Search       │
├─────────────────────────────────────────────┤
│          Layer 1: Collectors                 │
│  costs · processes · sessions · ghostty ·    │
│  network · windows · qdrant · orchestrator   │
└─────────────────────────────────────────────┘
```

---

## ✨ Premium Features

### 🧠 Smart Model Routing
Task too simple for Opus? AGIMON auto-downgrades:

| Task | Model | Cost/1K |
|------|-------|---------|
| Architecture review | Opus 4.6 | $0.075 |
| Regular coding | Sonnet 4.6 | $0.015 |
| File search, formatting | Haiku 4.5 | $0.001 |
| Research drafts, brainstorm | Local MLX | $0.000 |

Learns from past successes via Qdrant — if Haiku solved it last time, it won't waste Opus tokens.

### 🖥 Ghostty Native
Not just "terminal monitor" — full AppleScript API integration:
- **1 batched call** reads 20+ terminals (not 50 individual `osascript` calls)
- **Click-to-focus** any terminal across macOS Spaces
- **Read terminal content** via `perform action`
- **Send commands** to any terminal from the CLI
- **Map PIDs to windows** — see which Claude runs where

### 📋 Session Deep-Dive
- Subagent tree: model, tools, tokens per agent
- Tool usage: `Bash(12), Read(8), Write(3)` — see what agents do
- One-click: open project in Finder / IDE / Terminal / Claude

### 🌐 Network Intelligence
- SSH tunnel decoder: `:6333` = Qdrant, `:5432` = PostgreSQL
- External connections grouped by app
- Click any port → opens in browser

### ⭐ One-Click Project Launch
Every session, every terminal → submenu:
- 📂 Open in Finder
- 📝 Open in IDE
- 💻 Launch Claude Code here
- ⌨️ New terminal here

### 💰 Cost Analytics
14-day breakdown with bar charts, per-day granularity, exportable JSON.

---

## 🔌 Integrations

| Tool | How |
|------|-----|
| **Ghostty** | AppleScript API — windows, tabs, terminals, focus, content |
| **Qdrant** | Semantic search over session history |
| **Raycast** | Extension: 4 commands + menubar |
| **SketchyBar** | Desktop widgets |
| **Claude Code** | Session JSONL parsing, subagent tracking |
| **context-mode** | Token-efficient MCP data gathering |
| **RTK** | CLI output compression |
| **Ollama/MLX** | Local model routing |

---

## 📁 Structure

```
agimon/
├── agimon                  # CLI (bash)
├── app.py                  # TUI — 7 tabs (Textual)
├── menubar.py              # macOS menubar (rumps)
├── orchestrator.py         # Smart delegation engine
├── collectors/
│   ├── costs.py            # Token/cost analytics
│   ├── processes.py        # Process monitor (all dev tools)
│   ├── sessions.py         # Session + subagent parser
│   ├── ghostty.py          # Ghostty AppleScript bridge
│   ├── network.py          # SSH tunnels, services
│   ├── windows.py          # Global window search + focus
│   └── qdrant_store.py     # Qdrant semantic search
├── hooks/                  # Auto-indexing hooks
├── sketchybar/             # Desktop widgets
└── raycast-extension/      # Raycast commands
```

---

## 🤝 Works With Any Terminal

Built for **Ghostty** but process monitoring, cost tracking, and session analysis work everywhere. The orchestrator routes to:

- Claude Code (Opus / Sonnet / Haiku)
- Gemini CLI / Codex CLI
- Ollama / MLX (local, $0)
- Any CLI tool in a terminal

---

<p align="center">
  <strong>Built with ⚡ by <a href="https://github.com/supersynergy">supersynergy</a></strong><br/>
  <sub>Because running 10 AI agents without a dashboard is like flying blind.</sub><br/><br/>
  <em>Gotta monitor 'em all.</em>
</p>

---

## 🔎 Why AGIMON?

**If you use any of these, you need AGIMON:**

- **Claude Code** (Anthropic CLI) — monitor sessions, subagents, tool calls, token costs
- **Gemini CLI** (Google) — track processes, CPU, memory usage
- **OpenAI Codex CLI** — see what's running, what it costs
- **Kimi Code** (Moonshot) — process monitoring and control
- **Cursor / Windsurf / Continue** — IDE agent tracking
- **Aider** — monitor background coding agents
- **Any AI CLI tool** — if it runs in a terminal, AGIMON sees it

### Compared to alternatives

| | AGIMON | Activity Monitor | htop | AgentOps | Langfuse |
|---|---|---|---|---|---|
| AI agent awareness | ✅ | ❌ | ❌ | ✅ | ✅ |
| Terminal integration | ✅ Ghostty native | ❌ | ❌ | ❌ | ❌ |
| No SDK required | ✅ | ✅ | ✅ | ❌ needs SDK | ❌ needs SDK |
| macOS menubar | ✅ | ❌ | ❌ | ❌ | ❌ |
| Cost tracking | ✅ | ❌ | ❌ | ✅ | ✅ |
| Semantic search | ✅ Qdrant | ❌ | ❌ | ❌ | ❌ |
| Process kill/focus | ✅ | ✅ | ✅ | ❌ | ❌ |
| One-click project launch | ✅ | ❌ | ❌ | ❌ | ❌ |
| Subagent tracking | ✅ | ❌ | ❌ | ✅ | ✅ |
| SSH tunnel awareness | ✅ | ❌ | ❌ | ❌ | ❌ |
| Free & open source | ✅ | ✅ | ✅ | freemium | freemium |
| Zero config | ✅ | ✅ | ✅ | ❌ | ❌ |

### Who is this for?

- Developers running **multiple Claude Code instances** simultaneously
- Power users with **10+ terminal tabs** who lose track of what's where
- Teams who need to **track AI agent costs** across projects
- Anyone who wants **htop but for AI agents**

---

<details>
<summary><strong>📇 Keywords</strong> <em>(for GitHub search)</em></summary>

claude code monitor, ai agent dashboard, htop for ai, claude code dashboard,
ai process manager, terminal monitor macos, ghostty applescript, claude cost tracker,
ai agent orchestration, multi agent monitor, claude subagent tracking, ai coding assistant monitor,
gemini cli monitor, codex cli dashboard, kimi code monitor, aider monitor,
cursor agent tracker, windsurf process monitor, continue dev monitor,
macos menubar app, raycast extension ai, sketchybar plugin, textual tui dashboard,
qdrant semantic search, ai session history, token cost analytics, ssh tunnel monitor,
claude code cost, anthropic api monitor, openai cost tracker, ai developer tools,
process manager macos, terminal dashboard, agent memory, ai observability,
llm monitor, llm cost tracker, llm dashboard, claude opus monitor, claude sonnet tracker,
ai agent memory manager, smart agent monitor, agi monitor, agent orchestration platform

</details>
