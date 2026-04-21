"""AGIMON — macOS Menubar. Power-user AI-native UX."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
import urllib.request as _ur
from pathlib import Path

import rumps

sys.path.insert(0, os.path.dirname(__file__))

from AppKit import (
    NSAlert, NSAlertFirstButtonReturn,
    NSAttributedString, NSMutableAttributedString,
    NSFont, NSColor, NSTextField, NSImage,
    NSFontAttributeName, NSForegroundColorAttributeName,
)
from Foundation import NSDictionary, NSMakeRect

from collectors.processes import get_system_summary as _raw_get_system_summary
from collectors.costs import total_summary as _raw_total_summary, load_costs_by_day as _raw_load_costs_by_day
from collectors.network import (
    get_ssh_tunnels as _raw_get_ssh_tunnels,
    get_external_connections as _raw_get_external_connections,
    get_listening_services as _raw_get_listening_services,
)
from collectors.sessions import (
    load_recent_sessions as _raw_load_recent_sessions,
    get_active_session_ids as _raw_get_active_session_ids,
    Session,
)
from collectors.ghostty import (
    get_all_terminals_flat as _raw_get_all_terminals_flat,
    get_windows as _raw_get_windows,
    focus_terminal,
)
import collectors.telepathy as _tel
import collectors.llm as _llm
from collectors.cache import ttl_cache

# TTL-cached wrappers — dedupe within a tick + short-term caching across ticks.
# Menu rebuilds every 15s (_tick_count % 3 == 1 @ 5s timer); these TTLs are
# tuned so cache lasts through ~2 rebuilds for stable data.
get_system_summary        = ttl_cache(4.0)(_raw_get_system_summary)
total_summary             = ttl_cache(30.0)(_raw_total_summary)
load_costs_by_day         = ttl_cache(30.0)(_raw_load_costs_by_day)
get_ssh_tunnels           = ttl_cache(20.0)(_raw_get_ssh_tunnels)
get_external_connections  = ttl_cache(20.0)(_raw_get_external_connections)
get_listening_services    = ttl_cache(60.0)(_raw_get_listening_services)
load_recent_sessions      = ttl_cache(10.0)(_raw_load_recent_sessions)
get_active_session_ids    = ttl_cache(4.0)(_raw_get_active_session_ids)
get_windows               = ttl_cache(3.0)(_raw_get_windows)
get_all_terminals_flat    = ttl_cache(3.0)(_raw_get_all_terminals_flat)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ICON_PATH = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/ToolbarAdvanced.icns"
_KILL_ICON = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/AlertStopIcon.icns"

PROJECTS = [
    ("🤖 SuperJarvis",      "/Users/master/projects/SUPERJARVIS",                     "http://localhost:7777"),
    ("💼 SupersynergyCRM",  "/Users/master/SupersynergyCRM",                          "http://localhost:8000"),
    ("🕷 ZeroClaw Agents",  "/Users/master/supersynergyapp/supersynergy-agents",      None),
    ("🔍 Omni Scraper",     "/Users/master/omni-scraper",                             None),
    ("⚡ AGIMON",            SCRIPT_DIR,                                               None),
    ("📋 Plane.so",          "/Users/master/plane-docker",                             "http://localhost:8090"),
]

QUICK_SKILLS = [
    "/loop", "/review", "/security-review", "/claude-api",
]

QUICK_LINKS = [
    ("🖥 AGIMON TUI",       None),
    ("📊 Qdrant Dashboard", "http://localhost:6333/dashboard"),
    ("🤖 SuperJarvis :7777","http://localhost:7777"),
    ("💼 CRM :8000",        "http://localhost:8000"),
    ("📋 Plane.so :8090",   "http://localhost:8090"),
    ("📊 Grafana :3030",    "http://localhost:3030"),
    ("🔍 Typesense :8108",  "http://localhost:8108"),
    ("📦 Minio :9001",      "http://localhost:9001"),
    ("🌐 Gitea :3000",      "http://localhost:3000"),
    ("🦙 Ollama :11434",    "http://localhost:11434"),
]


# ── Formatting helpers ──────────────────────────────────────────────

def fmt_tok(n: int) -> str:
    if n >= 1_000_000_000: return f"{n/1e9:.1f}B"
    if n >= 1_000_000:     return f"{n/1e6:.1f}M"
    if n >= 1_000:         return f"{n/1e3:.1f}K"
    return str(n)


def _bar(val: float, mx: float, w: int = 10) -> str:
    if mx <= 0: return ""
    f = int((val / mx) * w)
    return "█" * f + "░" * (w - f)


def _trunc(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


def _proj_name(raw: str) -> str:
    raw = (raw or "").rstrip("/")
    if not raw:
        return "?"
    if raw.startswith("-"):
        parts = [p for p in raw.lstrip("-").split("-") if p]
        if len(parts) >= 2 and parts[0] == "Users":
            parts = parts[2:]
        return parts[-1] if parts else "home"
    return os.path.basename(raw)


def _session_label(s: Session, active_ids: set) -> str:
    proj = _proj_name(s.project)
    msg  = _trunc(s.first_user_message or "…", 32)
    tool_count = sum(s.tools_used.values())
    dot  = "●" if s.session_id in active_ids else "○"
    tc   = f" · {tool_count}t" if tool_count else ""
    return f"{dot} {proj} · {msg}{tc}"


def _window_label(t: dict) -> str:
    cwd   = t.get("working_dir", "")
    base  = os.path.basename(cwd.rstrip("/")) if cwd else "?"
    title = t.get("terminal_title", "")
    if title.startswith(base):
        title = title[len(base):].lstrip(" -–·|")
    suffix = f" · {_trunc(title, 20)}" if title else ""
    claude = " ●" if "claude" in (t.get("terminal_title","")).lower() else ""
    return f"{base}{suffix}{claude}"


def _ping(url: str) -> bool:
    try:
        _ur.urlopen(url, timeout=1)
        return True
    except Exception:
        return False


# ── Native macOS Alert ──────────────────────────────────────────────

def _styled_alert(title: str, lines: list[tuple[str, object]],
                  buttons: list[str] | None = None,
                  icon_path: str = _ICON_PATH) -> int:
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setAlertStyle_(1)
    for btn in (buttons or ["OK"]):
        alert.addButtonWithTitle_(btn)
    icon = NSImage.alloc().initByReferencingFile_(icon_path)
    if icon:
        alert.setIcon_(icon)
    text = NSMutableAttributedString.alloc().init()
    mono = NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0)
    for line, color in lines:
        attrs = NSDictionary.dictionaryWithObjects_forKeys_(
            [mono, color], [NSFontAttributeName, NSForegroundColorAttributeName])
        seg = NSAttributedString.alloc().initWithString_attributes_(line + "\n", attrs)
        text.appendAttributedString_(seg)
    tv = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 450, 280))
    tv.setAttributedStringValue_(text)
    tv.setEditable_(False)
    tv.setBezeled_(False)
    tv.setDrawsBackground_(False)
    tv.setSelectable_(True)
    alert.setAccessoryView_(tv)
    return alert.runModal()


def _daemon_show_logs(key: str, daemons_bin: str) -> None:
    try:
        r = subprocess.run([daemons_bin, "logs", key], capture_output=True,
                           text=True, timeout=5)
        import re
        out = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout + r.stderr)
        _result_alert(f"📝 Logs · {key}", out or "(leer)", copy_button=False)
    except Exception as e:
        _simple_alert("❌ logs", str(e))


def _run_show(cmd: str) -> None:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        import re
        out = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout + r.stderr)
        _result_alert(f"$ {cmd}", out or "(leer)")
    except Exception as e:
        _simple_alert("❌", str(e))


def _result_alert(title: str, content: str,
                  copy_button: bool = True,
                  max_chars: int = 20_000) -> None:
    """Scrollable result window via NSTextView in NSScrollView.
    Button 'Kopieren' copies content to clipboard (default).
    Handles long LLM outputs without overflowing the alert."""
    from AppKit import NSScrollView, NSTextView, NSMakeSize
    content = (content or "(leer)").strip()[:max_chars]
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setAlertStyle_(1)
    if copy_button:
        alert.addButtonWithTitle_("Kopieren")
    alert.addButtonWithTitle_("Schließen")
    icon = NSImage.alloc().initByReferencingFile_(_ICON_PATH)
    if icon:
        alert.setIcon_(icon)

    # Size scales with content length (capped)
    lines = content.count("\n") + max(1, len(content) // 70)
    h = min(520, max(160, lines * 16))
    w = 520

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(False)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)  # NSBezelBorder

    tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    tv.setEditable_(False)
    tv.setSelectable_(True)
    tv.setRichText_(False)
    tv.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0))
    tv.setTextColor_(NSColor.labelColor())
    tv.setString_(content)
    tv.setTextContainerInset_(NSMakeSize(6.0, 6.0))
    scroll.setDocumentView_(tv)
    alert.setAccessoryView_(scroll)

    r = alert.runModal()
    if copy_button and r == NSAlertFirstButtonReturn:
        subprocess.run(["pbcopy"], input=content.encode(), check=False)
        rumps.notification("AGIMON", "In Zwischenablage", _trunc(content, 60))


def _simple_alert(title: str, message: str) -> None:
    """Short info alert. For long/LLM output use _result_alert."""
    if len(message) > 400 or message.count("\n") > 6:
        _result_alert(title, message)
    else:
        _styled_alert(title, [(message, NSColor.labelColor())])


def _input_dialog(title: str, placeholder: str = "",
                  message: str = "", default: str = "",
                  multiline: bool = False) -> str | None:
    """Polished input via rumps.Window (wraps NSAlert + editable field).
    multiline=True → larger textbox for prompts/messages."""
    dims = (420, 140) if multiline else (420, 24)
    win = rumps.Window(
        message=message or placeholder,
        title=title,
        default_text=default,
        ok="Senden",
        cancel="Abbrechen",
        dimensions=dims,
    )
    icon = NSImage.alloc().initByReferencingFile_(_ICON_PATH)
    if icon:
        win._alert.setIcon_(icon)
    resp = win.run()
    if resp.clicked:
        val = (resp.text or "").strip()
        return val or None
    return None


# ── Callback factories ──────────────────────────────────────────────

def _noop(_): pass

def _copy(text: str):
    def cb(_):
        subprocess.run(["pbcopy"], input=text.encode(), check=False)
        rumps.notification("AGIMON", "Kopiert", text[:50])
    return cb

def _kill_pid(pid: int, label: str):
    def cb(_):
        r = _styled_alert(
            f"❌ {label} beenden?",
            [(f"PID: {pid}", NSColor.systemRedColor()),
             (f"Prozess: {label}", NSColor.labelColor()),
             ("", NSColor.labelColor()),
             ("Wird mit SIGTERM beendet.", NSColor.secondaryLabelColor())],
            buttons=["Kill", "Abbrechen"],
            icon_path=_KILL_ICON,
        )
        if r == NSAlertFirstButtonReturn:
            subprocess.run(["kill", str(pid)], check=False)
            rumps.notification("AGIMON", "Beendet", f"{label} PID {pid}")
    return cb

def _proc_detail(pid: int):
    def cb(_):
        try:
            ps_out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "pid,ppid,%cpu,%mem,rss,etime,command"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            net_out = subprocess.run(
                ["lsof", "-i", "-nP", "-a", "-p", str(pid)],
                capture_output=True, text=True, timeout=5).stdout.strip()
            conns = net_out.split("\n")[1:8]
            lines: list[tuple[str, object]] = []
            ps_lines = ps_out.split("\n")
            if len(ps_lines) >= 2:
                lines.append(("── Prozess ────────────────────────────────", NSColor.systemOrangeColor()))
                lines.append((ps_lines[0].strip(), NSColor.secondaryLabelColor()))
                cols = ps_lines[1].split()
                cpu_val = float(cols[2]) if len(cols) > 2 else 0
                color = (NSColor.systemRedColor() if cpu_val > 50
                         else NSColor.systemYellowColor() if cpu_val > 10
                         else NSColor.systemGreenColor())
                lines.append((ps_lines[1].strip(), color))
                if len(cols) >= 7:
                    lines.append(("", NSColor.labelColor()))
                    lines.append(("── Command ────────────────────────────────", NSColor.systemOrangeColor()))
                    lines.append((cols[6][:80], NSColor.systemCyanColor()))
            lines.append(("", NSColor.labelColor()))
            lines.append((f"── Netzwerk ({len(conns)} Verbindungen) ────────────────", NSColor.systemOrangeColor()))
            if conns:
                for c in conns:
                    parts = c.split()
                    addr = parts[8] if len(parts) > 8 else parts[-1] if parts else c
                    if "ESTABLISHED" in c:
                        lines.append((f"  ● {addr}", NSColor.systemGreenColor()))
                    elif "LISTEN" in c:
                        lines.append((f"  ○ {addr}", NSColor.systemBlueColor()))
                    else:
                        lines.append((f"  • {c.strip()[:60]}", NSColor.secondaryLabelColor()))
            else:
                lines.append(("  Keine Netzwerkverbindungen", NSColor.secondaryLabelColor()))
            r = _styled_alert(f"🔍 Prozess {pid}", lines,
                              buttons=["Kill", "PID kopieren", "CMD kopieren", "Schließen"])
            if r == NSAlertFirstButtonReturn:
                subprocess.run(["kill", str(pid)], check=False)
                rumps.notification("AGIMON", "Beendet", f"PID {pid}")
            elif r == NSAlertFirstButtonReturn + 1:
                subprocess.run(["pbcopy"], input=str(pid).encode(), check=False)
            elif r == NSAlertFirstButtonReturn + 2:
                cols2 = ps_lines[1].split(None, 6) if len(ps_lines) >= 2 else []
                cmd = cols2[6] if len(cols2) >= 7 else str(pid)
                subprocess.run(["pbcopy"], input=cmd.encode(), check=False)
        except Exception as e:
            _styled_alert("❌ Fehler", [(str(e), NSColor.systemRedColor())])
    return cb

def _focus_ghost(wi: int, ti: int = 1):
    def cb(_): focus_terminal(wi, ti)
    return cb

def _resume_session(session_id: str, cwd: str = "/Users/master"):
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{cwd}"
                set command of cfg to "/Users/master/.local/bin/claude --resume {session_id} --dangerously-skip-permissions"
                new window with configuration cfg
            end tell
        '''])
    return cb

def _open_url(url: str):
    def cb(_): subprocess.Popen(["open", url])
    return cb

def _open_in_finder(path: str):
    def cb(_): subprocess.Popen(["open", path])
    return cb

def _open_in_ide(path: str):
    def cb(_): subprocess.Popen(["windsurf", path])
    return cb

def _open_in_ghostty(path: str):
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{path}"
                new window with configuration cfg
            end tell
        '''])
    return cb

def _launch_claude_in(path: str):
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{path}"
                set command of cfg to "/Users/master/.local/bin/claude --dangerously-skip-permissions"
                new window with configuration cfg
            end tell
        '''])
    return cb

def _launch_ggcoder_in(path: str):
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{path}"
                set command of cfg to "ggcoder"
                new window with configuration cfg
            end tell
        '''])
    return cb

def _telepathy_jump(sid8: str):
    def cb(_):
        result = _tel.jump(sid8)
        rumps.notification("AGIMON Telepathy", "Jump", result[:80])
    return cb

def _run_skill_in(path: str, skill: str):
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{path}"
                set command of cfg to "/Users/master/.local/bin/claude --dangerously-skip-permissions {skill}"
                new window with configuration cfg
            end tell
        '''])
    return cb

def _run_in_ghostty_cmd(cmd: str, cwd: str = "/Users/master"):
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{cwd}"
                set command of cfg to "{cmd}"
                new window with configuration cfg
            end tell
        '''])
    return cb


# ── Async LLM helpers (always non-blocking) ─────────────────────────

def _async_minimax_alert(prompt: str, title: str):
    """Fire MiniMax call, show result as native alert when done."""
    def cb(_):
        def _show(result: str):
            if result:
                _simple_alert(title, result)
            else:
                _simple_alert(title, "Kein Ergebnis (API-Key fehlt?)")
        _llm.run_async(_llm.minimax_chat, _show, prompt,
                       "Du bist ein hilfreicher Assistent. Antworte auf Deutsch, prägnant.")
        rumps.notification("AGIMON", title, "Anfrage gesendet…")
    return cb

def _async_ollama_alert(prompt: str, title: str, model: str = "gemma3:270m"):
    def cb(_):
        def _show(result: str):
            if result:
                _simple_alert(title, result)
            else:
                _simple_alert(title, "Kein Ergebnis (Ollama läuft?)")
        _llm.run_async(_llm.ollama_quick, _show, prompt, model)
        rumps.notification("AGIMON", title, "Lokales LLM…")
    return cb

def _minimax_prompt_dialog(title: str = "⚡ Prompt → MiniMax"):
    def cb(_):
        text = _input_dialog(
            title,
            message="MiniMax M2.7-highspeed · ~1-3s · Ergebnis in Alert",
            placeholder="z.B. fasse zusammen was ich heute gemacht habe",
            multiline=True,
        )
        if text:
            _async_minimax_alert(text, title)(None)
    return cb

def _ollama_prompt_dialog(model: str = "gemma3:270m"):
    def cb(_):
        text = _input_dialog(
            f"🧠 Prompt → {model}",
            message=f"Lokal via Ollama · {model} · sub-second",
            placeholder="kurze frage oder klassifikation",
            multiline=True,
        )
        if text:
            _async_ollama_alert(text, f"🧠 {model}", model)(None)
    return cb

def _uda_ask_dialog():
    def cb(_):
        text = _input_dialog(
            "🔎 uda ask",
            message="Durchsucht deine Qdrant-KB (161k+ chunks)",
            placeholder="z.B. ideen für saas landing page",
        )
        if text and text.strip():
            subprocess.Popen(["osascript", "-e", f'''
                tell application "Ghostty"
                    activate
                    set cfg to new surface configuration
                    set command of cfg to "uda ask \\"{text.strip()}\\""
                    new window with configuration cfg
                end tell
            '''])
    return cb

def _syn_search_dialog():
    def cb(_):
        text = _input_dialog(
            "📡 syn search",
            message="FTS5 + Vektor-Suche im Synapse Brain",
            placeholder="suchbegriff",
        )
        if text and text.strip():
            subprocess.Popen(["osascript", "-e", f'''
                tell application "Ghostty"
                    activate
                    set cfg to new surface configuration
                    set command of cfg to "syn search \\"{text.strip()}\\""
                    new window with configuration cfg
                end tell
            '''])
    return cb

def _hyperfetch_dialog():
    def cb(_):
        text = _input_dialog(
            "🌐 hyperfetch URL",
            message="Anti-Bot Fetch (camoufox) · 0.07s · Markdown-Output",
            placeholder="https://…",
        )
        if text and text.strip():
            subprocess.Popen(["osascript", "-e", f'''
                tell application "Ghostty"
                    activate
                    set cfg to new surface configuration
                    set command of cfg to "hyperfetch \\"{text.strip()}\\" --stage camoufox"
                    new window with configuration cfg
                end tell
            '''])
    return cb


# ── Reusable submenu builders ───────────────────────────────────────

def _session_actions_submenu(item: rumps.MenuItem, s: Session) -> None:
    """Attach expanded session actions to item."""
    # Focus window via telepathy if possible
    sid8 = s.session_id[:8] if s.session_id else ""
    if sid8:
        item.add(rumps.MenuItem("🪟 Fokus Fenster",
                                callback=_telepathy_jump(sid8)))
    item.add(rumps.MenuItem("▶️ Resume in neuem Fenster",
                            callback=_resume_session(s.session_id, s.project)))
    # Transcript path
    proj_key = s.project.replace("/", "-").lstrip("-") if s.project else ""
    jsonl_path = str(Path.home() / ".claude/projects" / proj_key / f"{s.session_id}.jsonl")
    item.add(rumps.MenuItem("📋 Session-Pfad kopieren",
                            callback=_copy(jsonl_path)))
    item.add(rumps.MenuItem("📝 Transcript öffnen", callback=_open_transcript(jsonl_path)))

    # MiniMax summarize
    first_msg = _trunc(s.first_user_message or "", 300)
    tools_str = ", ".join(f"{t}({c})" for t, c in
                          sorted(s.tools_used.items(), key=lambda x: -x[1])[:5])
    summary_prompt = (f"Session-Zusammenfassung:\nErste Nachricht: {first_msg}\n"
                      f"Tools: {tools_str}\nGib eine kurze prägnante Zusammenfassung (3 Sätze).")
    item.add(rumps.MenuItem("🧠 Zusammenfassen (MiniMax)",
                            callback=_async_minimax_alert(summary_prompt, "🧠 Session-Zusammenfassung")))
    item.add(rumps.MenuItem("⚡ Auto-Tag (Ollama)",
                            callback=_async_ollama_alert(
                                f"Klassifiziere diese Claude-Session in 3 Tags (kommagetrennt): {first_msg}",
                                "⚡ Auto-Tag")))

    if first_msg:
        item.add(rumps.MenuItem("🔎 In Synapse suchen",
                                callback=_run_in_ghostty_cmd(
                                    f"syn search \"{first_msg[:40]}\"", s.project or "/Users/master")))
    if sid8:
        item.add(rumps.MenuItem("📡 Telepathy dieser Session",
                                callback=_run_in_ghostty_cmd(
                                    f"syn search telepathy {sid8}", s.project or "/Users/master")))

    if s.tools_used:
        tools = ", ".join(f"{t}({c})" for t, c in
                          sorted(s.tools_used.items(), key=lambda x: -x[1])[:5])
        item.add(rumps.MenuItem(f"🔧 Tools: {tools}", callback=_noop))


def _open_transcript(path: str):
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set command of cfg to "bat \\"{path}\\""
                new window with configuration cfg
            end tell
        '''])
    return cb


def _project_actions_submenu(item: rumps.MenuItem, path: str, url: str | None = None) -> None:
    item.add(rumps.MenuItem("💻 Claude starten",      callback=_launch_claude_in(path)))
    item.add(rumps.MenuItem("⚡ ggcoder starten",      callback=_launch_ggcoder_in(path)))
    item.add(rumps.MenuItem("📝 In Windsurf öffnen",  callback=_open_in_ide(path)))
    item.add(rumps.MenuItem("⌨️ Terminal hier",         callback=_open_in_ghostty(path)))
    item.add(rumps.MenuItem("📂 Im Finder",            callback=_open_in_finder(path)))
    if url:
        item.add(rumps.MenuItem("🌐 Web UI öffnen",   callback=_open_url(url)))
    proj_base = os.path.basename(path.rstrip("/"))
    item.add(rumps.MenuItem("🔎 uda ask (letzte 7d)",
                            callback=_run_in_ghostty_cmd(
                                f"uda ask \"what changed in {proj_base} last 7d\"")))
    # Stats
    item.add(rumps.MenuItem("📊 Stats (tokei + git)",
                            callback=_proj_stats(path)))
    # Skills submenu
    skills_sub = rumps.MenuItem("🚀 Skill ausführen", callback=_noop)
    for skill in QUICK_SKILLS:
        skills_sub.add(rumps.MenuItem(skill, callback=_run_skill_in(path, skill)))
    item.add(skills_sub)


def _proj_stats(path: str):
    def cb(_):
        try:
            tok_out = subprocess.run(["tokei", path], capture_output=True, text=True, timeout=10).stdout
            git_out = subprocess.run(["git", "-C", path, "status", "--short"],
                                     capture_output=True, text=True, timeout=5).stdout
            lines = []
            for l in tok_out.split("\n")[:12]:
                lines.append((l, NSColor.labelColor()))
            lines.append(("── git status ──────────────────────────", NSColor.systemOrangeColor()))
            for l in git_out.split("\n")[:10]:
                lines.append((l, NSColor.systemGreenColor()))
            _styled_alert(f"📊 {os.path.basename(path)}", lines)
        except Exception as e:
            _simple_alert("❌ Fehler", str(e))
    return cb


def _terminal_actions_submenu(item: rumps.MenuItem, t: dict) -> None:
    cwd = t.get("working_dir", "")
    wi  = t.get("window_index", 0)
    ti  = t.get("tab_index", 1)

    item.add(rumps.MenuItem("🪟 Fokus",              callback=_focus_ghost(wi, ti)))
    if cwd:
        item.add(rumps.MenuItem("📋 CWD kopieren",   callback=_copy(cwd)))
        item.add(rumps.MenuItem("📂 Im Finder",       callback=_open_in_finder(cwd)))
        item.add(rumps.MenuItem("📝 In Windsurf",     callback=_open_in_ide(cwd)))
        item.add(rumps.MenuItem("💻 Claude Code hier", callback=_launch_claude_in(cwd)))
        item.add(rumps.MenuItem("🔍 Was läuft? (MiniMax)", callback=_terminal_explain(t)))

        # Telepathy: other sessions in same cwd
        cwd_base = os.path.basename(cwd.rstrip("/"))
        item.add(rumps.MenuItem("📡 Telepathy: gleicher CWD",
                                callback=_run_in_ghostty_cmd(
                                    f"syn search telepathy {cwd_base}", cwd)))

        # Skills submenu
        skills_sub = rumps.MenuItem("🚀 Skill ausführen", callback=_noop)
        for skill in QUICK_SKILLS:
            skills_sub.add(rumps.MenuItem(skill, callback=_run_skill_in(cwd, skill)))
        item.add(skills_sub)


def _terminal_explain(t: dict):
    """Read terminal content, ask MiniMax what it does."""
    def cb(_):
        try:
            from collectors.ghostty import read_terminal_content
            content = read_terminal_content(t.get("window_index", 0), t.get("tab_index", 1))
        except Exception:
            content = t.get("terminal_title", "unbekanntes Terminal")
        content_trunc = _trunc(content or "leer", 600)
        prompt = f"Was macht dieses Terminal? Erkläre kurz (2 Sätze):\n{content_trunc}"
        _async_minimax_alert(prompt, "🔍 Terminal-Erklärung")(None)
    return cb


# ── Main App ────────────────────────────────────────────────────────

class ClaudeMenubar(rumps.App):

    def __init__(self) -> None:
        super().__init__(name="AGIMON", title="⚡ …", quit_button=None)
        self._cpu_history: list[float] = [0.0] * 8
        self._tick_count = 0

    @rumps.timer(5)
    def _tick(self, _):
        self._tick_count += 1
        try:
            si  = get_system_summary()
            act = si.get("active", 0)
            cpu = si.get("total_cpu", 0)
            self._cpu_history.append(cpu)
            self._cpu_history = self._cpu_history[-8:]

            # Title: compact live stats (no costs)
            try:
                tel_events = _tel.fetch_events(20)
                tel_count = len(tel_events)
            except Exception:
                tel_count = 0

            try:
                terms = get_all_terminals_flat()
                term_count = len(terms)
            except Exception:
                term_count = 0

            # Watchdog issues
            watchdog_issues = 0
            try:
                from collectors.watchdog import get_health_issues
                watchdog_issues = len(get_health_issues())
            except Exception:
                pass

            if act > 0:
                self.title = f"⚡{act} 📡{tel_count} 💻{term_count}"
            else:
                self.title = f"○ 📡{tel_count} 💻{term_count}"
            if watchdog_issues > 0:
                self.title += f" ⚠{watchdog_issues}"

            if self._tick_count % 3 == 1:
                data = {
                    "si":         si,
                    "sessions":   load_recent_sessions(30),
                    "active_ids": get_active_session_ids(),
                    "terms":      terms,
                    "tunnels":    get_ssh_tunnels(),
                    "external":   get_external_connections(),
                    "listeners":  get_listening_services(),
                    "costs":      total_summary(14),
                    "days":       load_costs_by_day(7),
                    "tel_events": tel_events,
                    "tel_count":  tel_count,
                }
                self._render_menu(data)
        except Exception:
            self.title = "○ err"

    def _sparkline(self, values: list[float]) -> str:
        chars = "▁▂▃▄▅▆▇█"
        if not values: return ""
        mx = max(values) or 1
        return "".join(chars[min(int(v / mx * 7), 7)] for v in values)

    # ── Menu render ─────────────────────────────────────────────────

    def _render_menu(self, data: dict) -> None:
        self.menu.clear()

        si         = data.get("si", {})
        sessions   = data.get("sessions", [])
        active_ids = data.get("active_ids", set())
        tel_events = data.get("tel_events", [])
        tel_count  = data.get("tel_count", 0)

        # ── 🚀 Aktionen (top-level quick actions) ───────────────────
        self._add_actions_section()
        self.menu.add(None)

        # ── 📡 Telepathy ────────────────────────────────────────────
        self._add_telepathy_section(tel_events)
        self.menu.add(None)

        # ── 🟢 Aktive Sessions ──────────────────────────────────────
        self._add_sessions_section(sessions, active_ids)
        self.menu.add(None)

        # ── 💻 Ghostty Terminals ────────────────────────────────────
        self._add_terminals_section(data.get("terms", []))
        self.menu.add(None)

        # ── 💰 Kosten ───────────────────────────────────────────────
        self._add_costs_section(data.get("costs", {}), data.get("days", []))

        # ── 🌐 Netzwerk ─────────────────────────────────────────────
        self._add_network_section(data)

        # ── ⚡ AI Services ───────────────────────────────────────────
        self._add_ai_services_section()
        self.menu.add(None)

        # ── ⭐ Projekte ──────────────────────────────────────────────
        self._add_projects_section()

        # ── 🔗 Quick Links ───────────────────────────────────────────
        self._add_quick_links_section()
        self.menu.add(None)

        # ── ⚙ Settings / Daemons ────────────────────────────────────
        self._add_settings_section()
        self.menu.add(None)

        # ── Footer ──────────────────────────────────────────────────
        self.menu.add(rumps.MenuItem("🧨 Alle Claude stoppen", callback=self._stop_all))
        self.menu.add(rumps.MenuItem("Beenden", callback=rumps.quit_application))

    # ── Section builders ────────────────────────────────────────────

    def _add_actions_section(self) -> None:
        sec = rumps.MenuItem("🚀 Aktionen", callback=_noop)
        sec.add(rumps.MenuItem("⚡ Prompt → MiniMax",        callback=_minimax_prompt_dialog()))
        sec.add(rumps.MenuItem("🧠 Prompt → gemma3:270m",    callback=_ollama_prompt_dialog("gemma3:270m")))
        sec.add(rumps.MenuItem("🧠 Prompt → smollm2:135m",   callback=_ollama_prompt_dialog("smollm2:135m")))
        sec.add(None)
        sec.add(rumps.MenuItem("🔎 uda ask …",               callback=_uda_ask_dialog()))
        sec.add(rumps.MenuItem("📡 syn search …",            callback=_syn_search_dialog()))
        sec.add(rumps.MenuItem("🌐 hyperfetch URL",           callback=_hyperfetch_dialog()))
        sec.add(None)
        sec.add(rumps.MenuItem("📥 miniflux-digest heute",
                               callback=_run_in_ghostty_cmd("miniflux-digest --since 24h")))
        sec.add(rumps.MenuItem("📅 schedule list",
                               callback=_run_in_ghostty_cmd("schedule list")))
        sec.add(None)
        sec.add(rumps.MenuItem("🦙 Ollama Modelle …",        callback=self._show_ollama_models))
        self.menu.add(sec)

    def _show_ollama_models(self, _):
        try:
            r = _ur.urlopen("http://localhost:11434/api/tags", timeout=3)
            models = json.loads(r.read()).get("models", [])
            lines = [("── Geladene Modelle ─────────────────────", NSColor.systemOrangeColor())]
            for m in models:
                name = m.get("name", "?")
                sz   = m.get("size", 0) // (1024**2)
                lines.append((f"  {name}  ({sz}MB)", NSColor.labelColor()))
            _styled_alert("🦙 Ollama Modelle", lines)
        except Exception as e:
            _simple_alert("🦙 Ollama", f"Nicht erreichbar: {e}")

    def _add_telepathy_section(self, events: list) -> None:
        # Categorize: live (has Ghostty window) vs idle
        live_cwds: set[str] = set()
        try:
            for win in get_windows():
                for tab in win.tabs:
                    for term in tab.terminals:
                        wd = term.working_dir or ""
                        if wd:
                            live_cwds.add(os.path.basename(wd.rstrip("/")))
        except Exception:
            pass

        live_evs  = [e for e in events if os.path.basename(e.cwd.rstrip("/")) in live_cwds]
        idle_evs  = [e for e in events if os.path.basename(e.cwd.rstrip("/")) not in live_cwds]

        header = f"📡 Telepathy ({len(live_evs)} live, {len(events)} total)"
        sec = rumps.MenuItem(header, callback=_noop)

        def _add_tel_item(parent, ev):
            cwd_base = os.path.basename(ev.cwd.rstrip("/")) or ev.cwd
            is_live  = cwd_base in live_cwds
            dot = "●" if is_live else "○"
            kind_icon = {"prompt": "💬", "reply": "🤖", "tools": "🔧"}.get(ev.kind, "•")
            body = _trunc(ev.body, 38)
            label = f"{dot} {cwd_base} · {kind_icon} {body}"
            it = rumps.MenuItem(label, callback=_telepathy_jump(ev.sid8))
            it.add(rumps.MenuItem("⚡ Jump",            callback=_telepathy_jump(ev.sid8)))
            it.add(rumps.MenuItem("📋 Session-ID kopieren", callback=_copy(ev.sid8)))
            it.add(rumps.MenuItem("🧠 Was macht Session? (MiniMax)",
                                  callback=_async_minimax_alert(
                                      f"Was macht diese Claude-Session?\nCWD: {ev.cwd}\n"
                                      f"Letzte Aktivität: {_trunc(ev.body, 200)}",
                                      "🧠 Session-Status")))
            parent.add(it)

        if live_evs:
            sec.add(rumps.MenuItem("─ Live ─────────────────────", callback=_noop))
            for ev in live_evs[:5]:
                _add_tel_item(sec, ev)

        if idle_evs:
            sec.add(rumps.MenuItem("─ Idle ─────────────────────", callback=_noop))
            for ev in idle_evs[:5]:
                _add_tel_item(sec, ev)

        if not events:
            sec.add(rumps.MenuItem("  Keine Aktivität — Synapse läuft?", callback=_noop))
        else:
            sec.add(None)
            # MiniMax summary of full feed
            all_bodies = "\n".join(f"[{e.sid8}][{e.kind}] {_trunc(e.body, 60)}" for e in events[:12])
            sec.add(rumps.MenuItem("🧠 Zusammenfassung (MiniMax)",
                                   callback=_async_minimax_alert(
                                       f"Fasse diese Telepathy-Ereignisse zusammen (1 Absatz):\n{all_bodies}",
                                       "🧠 Telepathy Feed")))
            def _open_feed(_):
                subprocess.Popen(["osascript", "-e", f'''
                    tell application "Ghostty"
                        activate
                        set cfg to new surface configuration
                        set command of cfg to "{SCRIPT_DIR}/.venv/bin/python3 -m collectors.telepathy"
                        set initial working directory of cfg to "{SCRIPT_DIR}"
                        new window with configuration cfg
                    end tell
                '''])
            sec.add(rumps.MenuItem("Feed in Ghostty öffnen…", callback=_open_feed))

        self.menu.add(sec)

    def _add_sessions_section(self, sessions: list, active_ids: set) -> None:
        active_s = [s for s in sessions if s.session_id in active_ids]
        recent_s = [s for s in sessions if s.session_id not in active_ids]

        proj_counts: dict[str, int] = {}
        for s in active_s:
            k = _proj_name(s.project)
            proj_counts[k] = proj_counts.get(k, 0) + 1
        proj_seen: dict[str, int] = {}

        sec = rumps.MenuItem(f"🟢 Aktive Sessions ({len(active_s)})", callback=_noop)
        for s in active_s:
            proj = _proj_name(s.project)
            proj_seen[proj] = proj_seen.get(proj, 0) + 1
            if proj_counts.get(proj, 1) > 1:
                proj_label = f"{proj} ({proj_seen[proj]})"
            else:
                proj_label = proj
            msg  = _trunc(s.first_user_message or "…", 30)
            tc   = sum(s.tools_used.values())
            label = f"● {proj_label} · {msg}" + (f" · {tc}t" if tc else "")
            it = rumps.MenuItem(label, callback=_resume_session(s.session_id, s.project))
            _session_actions_submenu(it, s)
            it.add(None)
            it.add(rumps.MenuItem("💀 Prozess beenden",
                                  callback=_kill_session_by_id(s.session_id)))
            sec.add(it)

        if not active_s:
            sec.add(rumps.MenuItem("  Keine aktiven Sessions", callback=_noop))
        self.menu.add(sec)

        rec = rumps.MenuItem(f"📜 Letzte Sessions ({len(recent_s)})", callback=_noop)
        for s in recent_s[:15]:
            proj = _proj_name(s.project)
            ts   = (s.start_time or "")[:10]
            msg  = _trunc(s.first_user_message or "…", 28)
            label = f"○ {proj} · {msg}  {ts}"
            it = rumps.MenuItem(label, callback=_resume_session(s.session_id, s.project))
            _session_actions_submenu(it, s)
            rec.add(it)
        self.menu.add(rec)

    def _add_terminals_section(self, terms: list) -> None:
        sec = rumps.MenuItem(f"💻 Ghostty ({len(terms)})", callback=_noop)
        for t in terms[:15]:
            wi = t.get("window_index", 0)
            ti = t.get("tab_index", 1)
            label = _window_label(t)
            it = rumps.MenuItem(label, callback=_focus_ghost(wi, ti))
            _terminal_actions_submenu(it, t)
            sec.add(it)
        if not terms:
            sec.add(rumps.MenuItem("  Keine Terminals", callback=_noop))
        self.menu.add(sec)

    def _add_costs_section(self, sm: dict, days: list) -> None:
        total = sm.get("total_cost", 0)
        toks  = sm.get("total_tokens", 0)
        sec = rumps.MenuItem(
            f"💰 Kosten  ${total:,.2f} / 14d  ·  {fmt_tok(toks)} tok",
            callback=_noop,
        )
        if days:
            mx_c = max(d.cost for d in days) or 1
            for d in days:
                b = _bar(d.cost, mx_c, 8)
                sec.add(rumps.MenuItem(
                    f"{d.date}  {b}  ${d.cost:>6,.2f}  {d.sessions:>3}s",
                    callback=_noop,
                ))
        self.menu.add(sec)

    def _add_network_section(self, data: dict) -> None:
        tunnels   = data.get("tunnels", [])
        external  = data.get("external", [])
        listeners = data.get("listeners", [])
        non_ssh   = [l for l in listeners if l.process != "ssh"]

        sec = rumps.MenuItem(
            f"🌐 Netzwerk  {len(tunnels)}t · {len(non_ssh)}d · {len(external)}ext",
            callback=_noop,
        )

        sub_t = rumps.MenuItem(f"🔒 SSH Tunnels ({len(tunnels)})", callback=_noop)
        for t in tunnels:
            sub_t.add(rumps.MenuItem(
                f":{t.local_port} → {t.label or '?'}",
                callback=_open_url(f"http://localhost:{t.local_port}"),
            ))
        sec.add(sub_t)

        sub_l = rumps.MenuItem(f"📡 Lokale Dienste ({len(non_ssh)})", callback=_noop)
        for l in non_ssh[:10]:
            sub_l.add(rumps.MenuItem(
                f"{l.process} :{l.local_port}{' ' + l.label if l.label else ''}",
                callback=_open_url(f"http://localhost:{l.local_port}"),
            ))
        sec.add(sub_l)

        pc: dict[str, int] = {}
        for c in external:
            pc[c.process] = pc.get(c.process, 0) + 1
        sub_e = rumps.MenuItem(f"🌍 Extern ({len(external)})", callback=_noop)
        for proc, cnt in sorted(pc.items(), key=lambda x: -x[1])[:8]:
            sub_e.add(rumps.MenuItem(f"{proc}: {cnt}×", callback=_noop))
        sec.add(sub_e)

        self.menu.add(sec)

    def _add_ai_services_section(self) -> None:
        ol_ok = _ping("http://localhost:11434/")
        qd_ok = _ping("http://localhost:6333/collections")
        ol_dot = "🟢" if ol_ok else "🔴"
        qd_dot = "🟢" if qd_ok else "🔴"

        sec = rumps.MenuItem(
            f"⚡ AI Services  Ollama {ol_dot}  Qdrant {qd_dot}",
            callback=_noop,
        )

        ol_menu = rumps.MenuItem(f"🦙 Ollama {ol_dot}", callback=_noop)
        if ol_ok:
            try:
                r = _ur.urlopen("http://localhost:11434/api/tags", timeout=2)
                models = json.loads(r.read()).get("models", [])
                for m in models[:10]:
                    name = m.get("name", "?")
                    sz   = m.get("size", 0) // (1024**3)
                    mi = rumps.MenuItem(f"  {name}  ({sz}GB)", callback=_noop)
                    # One-click run quick test
                    mi.add(rumps.MenuItem("🧠 Testen",
                                         callback=_async_ollama_alert("Antworte mit 'OK'", f"Test {name}", name)))
                    ol_menu.add(mi)
            except Exception:
                pass
        else:
            def _start_ollama(_):
                subprocess.Popen(["/opt/homebrew/bin/ollama", "serve"])
            ol_menu.add(rumps.MenuItem("▶ Ollama starten", callback=_start_ollama))
        sec.add(ol_menu)

        qd_menu = rumps.MenuItem(f"🗄 Qdrant {qd_dot}", callback=_noop)
        if qd_ok:
            try:
                r = _ur.urlopen("http://localhost:6333/collections", timeout=2)
                colls = json.loads(r.read()).get("result", {}).get("collections", [])
                for c in colls[:12]:
                    qd_menu.add(rumps.MenuItem(f"  {c['name']}", callback=_noop))
            except Exception:
                pass
        else:
            def _start_qdrant(_):
                subprocess.Popen(["docker", "run", "-d",
                    "-p", "6333:6333", "-p", "6334:6334",
                    "-v", f"{Path.home()}/qdrant_storage:/qdrant/storage",
                    "qdrant/qdrant"])
                rumps.notification("Qdrant", "Docker Container", "wird gestartet…")
            qd_menu.add(rumps.MenuItem("▶ Qdrant (Docker) starten", callback=_start_qdrant))
        sec.add(qd_menu)

        # MiniMax status
        mm_key = _llm.minimax_key()
        mm_dot = "🟢" if mm_key else "🔴"
        mm_menu = rumps.MenuItem(f"🤖 MiniMax M2.7 {mm_dot}", callback=_noop)
        if mm_key:
            mm_menu.add(rumps.MenuItem("⚡ Test-Prompt senden",
                                       callback=_async_minimax_alert("Antworte mit 'OK'", "MiniMax Test")))
        else:
            mm_menu.add(rumps.MenuItem("  Key in ~/.gg/auth.json eintragen", callback=_noop))
        sec.add(mm_menu)

        self.menu.add(sec)

    def _add_projects_section(self) -> None:
        sec = rumps.MenuItem("⭐ Projekte", callback=_noop)
        for name, path, url in PROJECTS:
            if not os.path.isdir(path):
                continue
            it = rumps.MenuItem(name, callback=_open_in_finder(path))
            _project_actions_submenu(it, path, url)
            sec.add(it)
        self.menu.add(sec)

    def _add_quick_links_section(self) -> None:
        sec = rumps.MenuItem("🔗 Quick Links", callback=_noop)
        for label, url in QUICK_LINKS:
            if url is None:
                sec.add(rumps.MenuItem(label, callback=self._open_tui))
            else:
                sec.add(rumps.MenuItem(label, callback=_open_url(url)))
        self.menu.add(sec)

    def _add_settings_section(self) -> None:
        """⚙ Background daemons control + premium settings."""
        DAEMONS = [
            ("synapse",   "🧠 Synapse Brain",   "synapsed -f"),
            ("telepathy", "📡 Telepathy",       "telepathy_daemon.py"),
            ("agimon",    "⚡ Menubar",         "menubar.py"),
        ]
        sec = rumps.MenuItem("⚙ Einstellungen", callback=_noop)

        # ── 📦 Prozesse submenu ────────────────────────────────────
        procs_bin = os.path.join(SCRIPT_DIR, "procs")

        def _procs_cmd(args: list):
            def cb(_):
                try:
                    r = subprocess.run(
                        [procs_bin] + args,
                        capture_output=True, text=True, timeout=30,
                        cwd=SCRIPT_DIR,
                    )
                    import re as _re
                    out = _re.sub(r'\x1b\[[0-9;]*m', '', (r.stdout + r.stderr).strip())
                    _result_alert("📦 Prozesse", out or f"(no output) rc={r.returncode}", copy_button=False)
                except Exception as e:
                    _simple_alert("❌ procs", str(e))
            return cb

        pm = rumps.MenuItem("📦 Prozesse", callback=_noop)
        pm.add(rumps.MenuItem("📋 Übersicht alle Kategorien", callback=_procs_cmd(["list"])))
        pm.add(None)

        # Per-category submenus (static definition — counts updated at menu rebuild time)
        _CAT_CONFIG = [
            ("Autopilot",   "🤖 Autopilot"),
            ("ZeroClaw",    "🕷 ZeroClaw"),
            ("SuperJarvis", "🧠 SuperJarvis"),
            ("Daemon",      "📡 Daemons"),
            ("DevService",  "🛠 DevService"),
            ("Agent",       "💬 Agents"),
            ("System",      "🪪 System"),
            ("Other",       "❓ Andere"),
        ]
        for cat_key, cat_label in _CAT_CONFIG:
            csub = rumps.MenuItem(cat_label, callback=_noop)
            csub.add(rumps.MenuItem("📋 Alle anzeigen",    callback=_procs_cmd(["list", "--cat", cat_key])))
            csub.add(None)
            csub.add(rumps.MenuItem("⚡ Alle starten",      callback=_procs_cmd(["batch", "start",   cat_key])))
            csub.add(rumps.MenuItem("⏹ Alle stoppen",       callback=_procs_cmd(["batch", "stop",    cat_key])))
            csub.add(rumps.MenuItem("🔄 Alle neu starten",  callback=_procs_cmd(["batch", "restart", cat_key])))
            pm.add(csub)

        pm.add(None)
        pm.add(rumps.MenuItem("🏥 Heal (stalled services)",  callback=_procs_cmd(["heal"])))
        pm.add(rumps.MenuItem("📊 MiniMax Summary",          callback=_procs_cmd(["summary"])))
        pm.add(rumps.MenuItem("🔍 LLM-Klassifizieren",      callback=_procs_cmd(["classify"])))
        sec.add(pm)
        sec.add(None)

        # Daemons submenu
        dm = rumps.MenuItem("🔌 Daemons", callback=_noop)
        daemons_bin = os.path.join(SCRIPT_DIR, "daemons")

        def _daemon_cmd(action: str, name: str):
            def cb(_):
                try:
                    r = subprocess.run(
                        [daemons_bin, action, name],
                        capture_output=True, text=True, timeout=15,
                    )
                    out = (r.stdout + r.stderr).strip() or f"(no output) rc={r.returncode}"
                    # strip ANSI
                    import re
                    out = re.sub(r'\x1b\[[0-9;]*m', '', out)
                    rumps.notification("AGIMON daemons", f"{action} {name}", out[:100])
                except Exception as e:
                    _simple_alert("❌ daemons", str(e))
            return cb

        def _daemon_status_dialog(_):
            try:
                r = subprocess.run([daemons_bin, "status"], capture_output=True,
                                   text=True, timeout=5)
                import re
                out = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout)
                _result_alert("🔌 Daemon Status", out, copy_button=False)
            except Exception as e:
                _simple_alert("❌ status", str(e))

        dm.add(rumps.MenuItem("📋 Status-Übersicht", callback=_daemon_status_dialog))
        dm.add(None)

        for key, display, _pat in DAEMONS:
            sub = rumps.MenuItem(display, callback=_noop)
            sub.add(rumps.MenuItem("▶ Start",    callback=_daemon_cmd("start", key)))
            sub.add(rumps.MenuItem("⏹ Stop",     callback=_daemon_cmd("stop", key)))
            sub.add(rumps.MenuItem("🔄 Restart", callback=_daemon_cmd("restart", key)))
            sub.add(None)
            sub.add(rumps.MenuItem("✅ Autostart an",  callback=_daemon_cmd("enable", key)))
            sub.add(rumps.MenuItem("🚫 Autostart aus", callback=_daemon_cmd("disable", key)))
            sub.add(None)
            sub.add(rumps.MenuItem("📝 Logs",          callback=lambda _, k=key: _daemon_show_logs(k, daemons_bin)))
            dm.add(sub)

        dm.add(None)
        dm.add(rumps.MenuItem("⬆ Alle starten", callback=_daemon_cmd("all-start", "")))
        dm.add(rumps.MenuItem("⬇ Alle stoppen", callback=_daemon_cmd("all-stop", "")))
        dm.add(rumps.MenuItem("🔧 Bootstrap (plists installieren)",
                              callback=_daemon_cmd("bootstrap", "")))
        sec.add(dm)

        # Premium settings submenu
        cfg = rumps.MenuItem("💎 Premium Settings", callback=_noop)
        cfg.add(rumps.MenuItem("📝 Config editieren", callback=self._open_config_editor))
        cfg.add(rumps.MenuItem("📋 Config anzeigen",  callback=self._show_config))
        cfg.add(None)
        cfg.add(rumps.MenuItem("🔢 Menubar Refresh setzen", callback=self._set_refresh))
        cfg.add(rumps.MenuItem("📡 Telepathy Poll setzen",  callback=self._set_tel_poll))
        cfg.add(rumps.MenuItem("🧠 Ollama Default-Modell",  callback=self._set_ollama_model))
        sec.add(cfg)

        # Watchdog + diagnostics
        diag = rumps.MenuItem("🩺 Diagnose", callback=_noop)
        diag.add(rumps.MenuItem("🔍 Health Check", callback=self._run_watchdog))
        diag.add(rumps.MenuItem("📊 syn stats",    callback=lambda _: _run_show("syn stats")))
        diag.add(rumps.MenuItem("📊 ollama list",  callback=lambda _: _run_show("ollama list")))
        sec.add(diag)

        self.menu.add(sec)

    def _open_config_editor(self, _=None):
        subprocess.Popen(["open", "-t", os.path.expanduser("~/.agimon/config.json")])

    def _show_config(self, _=None):
        try:
            content = Path("~/.agimon/config.json").expanduser().read_text()
            _result_alert("💎 agimon config", content, copy_button=True)
        except Exception as e:
            _simple_alert("❌", str(e))

    def _set_refresh(self, _=None):
        val = _input_dialog("Menubar Refresh (Sekunden)",
                            message="Wie oft soll das Menü aktualisiert werden?",
                            default="5")
        if val and val.isdigit():
            subprocess.run([os.path.join(SCRIPT_DIR, "daemons"),
                           "config", "set", "menubar.refresh_seconds", val])
            rumps.notification("AGIMON", "Refresh gesetzt", f"{val}s — restart menubar")

    def _set_tel_poll(self, _=None):
        val = _input_dialog("Telepathy Poll (Sekunden)",
                            message="Wie oft soll der Daemon pollen? (4 = default)",
                            default="4")
        if val and val.isdigit():
            subprocess.run([os.path.join(SCRIPT_DIR, "daemons"),
                           "config", "set", "telepathy.poll_seconds", val])
            rumps.notification("AGIMON", "Telepathy Poll", f"{val}s — restart telepathy")

    def _set_ollama_model(self, _=None):
        val = _input_dialog("Ollama Default-Modell",
                            message="gemma3:270m · smollm2:135m · smollm2:360m · phi4-mini",
                            default="gemma3:270m")
        if val:
            subprocess.run([os.path.join(SCRIPT_DIR, "daemons"),
                           "config", "set", "menubar.default_ollama_model",
                           f'"{val}"'])
            rumps.notification("AGIMON", "Modell", val)

    def _run_watchdog(self, _=None):
        try:
            r = subprocess.run([os.path.join(SCRIPT_DIR, ".venv/bin/python3"),
                               os.path.join(SCRIPT_DIR, "collectors/watchdog.py")],
                              capture_output=True, text=True, timeout=12)
            import re
            out = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout + r.stderr)
            _result_alert("🩺 Health Check", out or "(leer)", copy_button=False)
        except Exception as e:
            _simple_alert("❌", str(e))

    # ── App actions ─────────────────────────────────────────────────

    def _open_tui(self, _=None):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set command of cfg to "{SCRIPT_DIR}/.venv/bin/python3 {SCRIPT_DIR}/app.py"
                set initial working directory of cfg to "{SCRIPT_DIR}"
                new window with configuration cfg
            end tell
        '''])

    def _stop_all(self, _):
        r = rumps.alert("Alle Claude-Instanzen stoppen?", ok="Kill All", cancel="Nein")
        if r == 1:
            subprocess.run(["pkill", "-f", "claude.*--dangerously"], check=False)
            rumps.notification("Claude Monitor", "", "Alle gestoppt")


def _kill_session_by_id(session_id: str):
    def cb(_):
        subprocess.run(["pkill", "-f", f"--resume {session_id}"], check=False)
        rumps.notification("AGIMON", "Session beendet", session_id[:16])
    return cb


if __name__ == "__main__":
    ClaudeMenubar().run()
