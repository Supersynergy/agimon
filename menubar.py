"""AGIMON — macOS Menubar. Polished power-user UX."""
from __future__ import annotations
import json
import os
import subprocess
import sys
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

from collectors.processes import get_system_summary
from collectors.costs import total_summary, load_costs_by_day
from collectors.network import get_ssh_tunnels, get_external_connections, get_listening_services
from collectors.sessions import load_recent_sessions, get_active_session_ids, Session
from collectors.ghostty import get_all_terminals_flat, get_windows, focus_terminal
import collectors.telepathy as _tel

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
    """Decode Claude Code's dash-encoded project key → human name.
    '-Users-master-projects-agimon' → 'agimon'; '-Users-master' → 'home'."""
    raw = (raw or "").rstrip("/")
    if not raw:
        return "?"
    if raw.startswith("-"):
        parts = [p for p in raw.lstrip("-").split("-") if p]
        if len(parts) >= 2 and parts[0] == "Users":
            parts = parts[2:]  # drop "Users", username
        return parts[-1] if parts else "home"
    return os.path.basename(raw)


def _session_label(s: Session, active_ids: set) -> str:
    """Human label: <project> · <prompt-snippet> · N tools"""
    proj = _proj_name(s.project)
    msg  = _trunc(s.first_user_message or "…", 32)
    tool_count = sum(s.tools_used.values())
    dot  = "●" if s.session_id in active_ids else "○"
    tc   = f" · {tool_count}t" if tool_count else ""
    return f"{dot} {proj} · {msg}{tc}"


def _window_label(t: dict) -> str:
    """Human label: <cwd-basename> · <title-hint>"""
    cwd   = t.get("working_dir", "")
    base  = os.path.basename(cwd.rstrip("/")) if cwd else "?"
    title = t.get("terminal_title", "")
    # strip duplicate basename from title
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
    tv = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 450, 220))
    tv.setAttributedStringValue_(text)
    tv.setEditable_(False)
    tv.setBezeled_(False)
    tv.setDrawsBackground_(False)
    tv.setSelectable_(True)
    alert.setAccessoryView_(tv)
    return alert.runModal()


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

def _telepathy_jump(sid8: str):
    def cb(_):
        result = _tel.jump(sid8)
        rumps.notification("AGIMON Telepathy", "Jump", result[:80])
    return cb


# ── Reusable submenu builders ───────────────────────────────────────

def _session_actions_submenu(item: rumps.MenuItem, s: Session) -> None:
    """Attach standard session actions to item (mutates in place)."""
    item.add(rumps.MenuItem("▶️ Resume in neuem Fenster",
                            callback=_resume_session(s.session_id, s.project)))
    item.add(rumps.MenuItem("📋 Session-ID kopieren", callback=_copy(s.session_id)))
    item.add(rumps.MenuItem("📋 Resume-Command kopieren",
                            callback=_copy(f"claude --resume {s.session_id} --dangerously-skip-permissions")))
    if s.tools_used:
        tools = ", ".join(f"{t}({c})" for t, c in
                          sorted(s.tools_used.items(), key=lambda x: -x[1])[:5])
        item.add(rumps.MenuItem(f"Tools: {tools}", callback=_noop))


def _project_actions_submenu(item: rumps.MenuItem, path: str, url: str | None = None) -> None:
    """Attach standard project actions to item (mutates in place)."""
    item.add(rumps.MenuItem("💻 Claude Code starten",  callback=_launch_claude_in(path)))
    item.add(rumps.MenuItem("📝 In Windsurf öffnen",   callback=_open_in_ide(path)))
    item.add(rumps.MenuItem("⌨️ Terminal hier",         callback=_open_in_ghostty(path)))
    item.add(rumps.MenuItem("📂 Im Finder",             callback=_open_in_finder(path)))
    if url:
        item.add(rumps.MenuItem("🌐 Web UI öffnen",    callback=_open_url(url)))


def _terminal_actions_submenu(item: rumps.MenuItem, t: dict) -> None:
    cwd = t.get("working_dir", "")
    if not cwd:
        return
    item.add(rumps.MenuItem("📂 Im Finder",          callback=_open_in_finder(cwd)))
    item.add(rumps.MenuItem("📝 In Windsurf",         callback=_open_in_ide(cwd)))
    item.add(rumps.MenuItem("💻 Claude Code starten", callback=_launch_claude_in(cwd)))
    item.add(rumps.MenuItem("📋 Pfad kopieren",       callback=_copy(cwd)))


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
            si = get_system_summary()
            act = si.get("active", 0)
            cpu = si.get("total_cpu", 0)
            self._cpu_history.append(cpu)
            self._cpu_history = self._cpu_history[-8:]

            # Title: concise live stats
            try:
                sm = total_summary(1)
                cost_today = sm.get("total_cost", 0)
                cost_str = f"${cost_today:.2f}"
            except Exception:
                cost_str = "$?.??"
            dot = "●" if act > 0 else "○"
            self.title = f"⚡ {act} · {cost_str}" if act > 0 else f"{dot} {cost_str}"

            if self._tick_count % 3 == 1:
                data = {
                    "si":        si,
                    "sessions":  load_recent_sessions(30),
                    "active_ids": get_active_session_ids(),
                    "terms":     get_all_terminals_flat(),
                    "tunnels":   get_ssh_tunnels(),
                    "external":  get_external_connections(),
                    "listeners": get_listening_services(),
                    "costs":     total_summary(14),
                    "days":      load_costs_by_day(7),
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

        si       = data.get("si", {})
        act      = si.get("active", 0)
        sessions = data.get("sessions", [])
        active_ids = data.get("active_ids", set())

        # ── 📡 Telepathy ────────────────────────────────────────────
        self._add_telepathy_section()
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

        # ── Footer ──────────────────────────────────────────────────
        self.menu.add(rumps.MenuItem("🧨 Alle Claude stoppen", callback=self._stop_all))
        self.menu.add(rumps.MenuItem("Beenden", callback=rumps.quit_application))

    # ── Section builders ────────────────────────────────────────────

    def _add_telepathy_section(self) -> None:
        try:
            events = _tel.fetch_events(8)
        except Exception:
            events = []

        # Precompute live cwds
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

        count_str = f" ({len(events)})" if events else ""
        sec = rumps.MenuItem(f"📡 Telepathy{count_str}", callback=_noop)

        if not events:
            sec.add(rumps.MenuItem("  Kein Aktivität — Synapse läuft?", callback=_noop))
        else:
            for ev in events:
                cwd_base = os.path.basename(ev.cwd.rstrip("/")) or ev.cwd
                dot = "●" if cwd_base in live_cwds else "○"
                kind_icon = {"prompt": "💬", "reply": "🤖", "tools": "🔧"}.get(ev.kind, "•")
                body = _trunc(ev.body, 42)
                label = f"{dot} {cwd_base} · {kind_icon} {body}"
                it = rumps.MenuItem(label, callback=_telepathy_jump(ev.sid8))
                sec.add(it)
            sec.add(None)
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
        active_s  = [s for s in sessions if s.session_id in active_ids]
        recent_s  = [s for s in sessions if s.session_id not in active_ids]

        # Disambiguate duplicate project names
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
            it.add(rumps.MenuItem(f"❌ Prozess beenden", callback=_noop))  # placeholder
            sec.add(it)

        if not active_s:
            sec.add(rumps.MenuItem("  Keine aktiven Sessions", callback=_noop))
        self.menu.add(sec)

        # Recent sessions (collapsed submenu)
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
        sess  = sm.get("total_sessions", 0)
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

        # Ollama
        ol_menu = rumps.MenuItem(f"🦙 Ollama {ol_dot}", callback=_noop)
        if ol_ok:
            try:
                r = _ur.urlopen("http://localhost:11434/api/tags", timeout=2)
                models = json.loads(r.read()).get("models", [])
                for m in models[:10]:
                    name = m.get("name", "?")
                    sz   = m.get("size", 0) // (1024**3)
                    ol_menu.add(rumps.MenuItem(f"  {name}  ({sz}GB)", callback=_noop))
            except Exception:
                pass
        else:
            def _start_ollama(_):
                subprocess.Popen(["/opt/homebrew/bin/ollama", "serve"])
            ol_menu.add(rumps.MenuItem("▶ Ollama starten", callback=_start_ollama))
        sec.add(ol_menu)

        # Qdrant
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


if __name__ == "__main__":
    ClaudeMenubar().run()
