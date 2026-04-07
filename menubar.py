"""AGIMON — macOS Menubar with native pyobjc dialogs."""
from __future__ import annotations
import rumps
import subprocess
import os
import sys

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
from collectors.sessions import load_recent_sessions, get_active_session_ids
from collectors.ghostty import get_all_terminals_flat, focus_terminal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Custom icon (not Python logo)
_ICON_PATH = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/ToolbarAdvanced.icns"
_KILL_ICON = "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/AlertStopIcon.icns"


def fmt_tok(n: int) -> str:
    if n >= 1_000_000_000: return f"{n/1e9:.1f}B"
    if n >= 1_000_000: return f"{n/1e6:.1f}M"
    if n >= 1_000: return f"{n/1e3:.1f}K"
    return str(n)


def _bar(val: float, mx: float, w: int = 10) -> str:
    if mx <= 0: return ""
    f = int((val / mx) * w)
    return "\u2588" * f + "\u2591" * (w - f)


# ── Native macOS Dialog ─────────────────────────────────────────────

def _styled_alert(title: str, lines: list[tuple[str, object]],
                  buttons: list[str] | None = None,
                  icon_path: str = _ICON_PATH) -> int:
    """Show a native NSAlert with colored monospace text and custom icon."""
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setAlertStyle_(1)  # Informational

    for btn in (buttons or ["OK"]):
        alert.addButtonWithTitle_(btn)

    icon = NSImage.alloc().initByReferencingFile_(icon_path)
    if icon:
        alert.setIcon_(icon)

    # Build attributed string
    text = NSMutableAttributedString.alloc().init()
    mono = NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0)

    for line, color in lines:
        attrs = NSDictionary.dictionaryWithObjects_forKeys_(
            [mono, color],
            [NSFontAttributeName, NSForegroundColorAttributeName],
        )
        seg = NSAttributedString.alloc().initWithString_attributes_(line + "\n", attrs)
        text.appendAttributedString_(seg)

    # Accessory text field
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
        result = _styled_alert(
            f"\u274c {label} beenden?",
            [
                (f"PID: {pid}", NSColor.systemRedColor()),
                (f"Prozess: {label}", NSColor.labelColor()),
                ("", NSColor.labelColor()),
                ("Wird mit SIGTERM beendet.", NSColor.secondaryLabelColor()),
            ],
            buttons=["Kill", "Abbrechen"],
            icon_path=_KILL_ICON,
        )
        if result == NSAlertFirstButtonReturn:
            subprocess.run(["kill", str(pid)], check=False)
            rumps.notification("AGIMON", "Beendet", f"{label} PID {pid}")
    return cb

def _proc_detail(pid: int):
    def cb(_):
        try:
            ps_out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "pid,ppid,%cpu,%mem,rss,etime,command"],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            net_out = subprocess.run(
                ["lsof", "-i", "-nP", "-a", "-p", str(pid)],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            conns = net_out.split("\n")[1:8]

            lines: list[tuple[str, object]] = []

            # Parse ps output
            ps_lines = ps_out.split("\n")
            if len(ps_lines) >= 2:
                header = ps_lines[0].strip()
                data = ps_lines[1].strip()
                lines.append((f"\u2500\u2500 Prozess \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", NSColor.systemOrangeColor()))
                lines.append((header, NSColor.secondaryLabelColor()))
                # Color code by CPU
                cols = data.split()
                cpu_val = float(cols[2]) if len(cols) > 2 else 0
                color = NSColor.systemRedColor() if cpu_val > 50 else (
                    NSColor.systemYellowColor() if cpu_val > 10 else NSColor.systemGreenColor()
                )
                lines.append((data, color))

            # Command
            if len(ps_lines) >= 2:
                cols = ps_lines[1].split(None, 6)
                if len(cols) >= 7:
                    lines.append(("", NSColor.labelColor()))
                    lines.append(("\u2500\u2500 Command \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", NSColor.systemOrangeColor()))
                    lines.append((cols[6][:80], NSColor.systemCyanColor()))

            # Network
            lines.append(("", NSColor.labelColor()))
            lines.append((f"\u2500\u2500 Netzwerk ({len(conns)} Verbindungen) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", NSColor.systemOrangeColor()))
            if conns:
                for c in conns:
                    parts = c.split()
                    if len(parts) >= 9:
                        addr = parts[8] if len(parts) > 8 else parts[-1]
                        if "ESTABLISHED" in c:
                            lines.append((f"  \u25cf {addr}", NSColor.systemGreenColor()))
                        elif "LISTEN" in c:
                            lines.append((f"  \u25cb {addr}", NSColor.systemBlueColor()))
                        else:
                            lines.append((f"  \u2022 {c.strip()[:60]}", NSColor.secondaryLabelColor()))
                    else:
                        lines.append((f"  {c.strip()[:60]}", NSColor.secondaryLabelColor()))
            else:
                lines.append(("  Keine Netzwerkverbindungen", NSColor.secondaryLabelColor()))

            result = _styled_alert(
                f"\U0001f50d Prozess {pid}",
                lines,
                buttons=["Kill", "PID kopieren", "CMD kopieren", "Schlie\u00dfen"],
            )

            if result == NSAlertFirstButtonReturn:
                subprocess.run(["kill", str(pid)], check=False)
                rumps.notification("AGIMON", "Beendet", f"PID {pid}")
            elif result == NSAlertFirstButtonReturn + 1:
                subprocess.run(["pbcopy"], input=str(pid).encode(), check=False)
                rumps.notification("AGIMON", "Kopiert", f"PID {pid}")
            elif result == NSAlertFirstButtonReturn + 2:
                cols = ps_lines[1].split(None, 6) if len(ps_lines) >= 2 else []
                cmd = cols[6] if len(cols) >= 7 else str(pid)
                subprocess.run(["pbcopy"], input=cmd.encode(), check=False)
                rumps.notification("AGIMON", "Kopiert", cmd[:50])

        except Exception as e:
            _styled_alert("\u274c Fehler", [(str(e), NSColor.systemRedColor())])
    return cb

def _focus_ghost(wi: int):
    def cb(_): focus_terminal(wi)
    return cb

def _resume_session(session_id: str, cwd: str = "/Users/master"):
    """One-click: Resume a Claude session in a new Ghostty window."""
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{cwd}"
                set command of cfg to "claude --resume {session_id} --dangerously-skip-permissions"
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

def _open_in_ide(path: str, ide: str = "Windsurf"):
    def cb(_):
        if ide == "Windsurf":
            subprocess.Popen(["windsurf", path])
        elif ide == "Sublime":
            subprocess.Popen(["subl", path])
        else:
            subprocess.Popen(["open", "-a", ide, path])
    return cb

def _open_in_ghostty(path: str, cmd: str = ""):
    """Open a new Ghostty window at path, optionally running a command."""
    def cb(_):
        init = f'set initial input of cfg to "{cmd}\\n"' if cmd else ""
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{path}"
                {init}
                new window with configuration cfg
            end tell
        '''])
    return cb

def _launch_claude_in(path: str):
    """One-click: Open Ghostty + Claude Code in project dir."""
    def cb(_):
        subprocess.Popen(["osascript", "-e", f'''
            tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "{path}"
                set command of cfg to "claude --dangerously-skip-permissions"
                new window with configuration cfg
            end tell
        '''])
    return cb


# ── Main App ────────────────────────────────────────────────────────

class ClaudeMenubar(rumps.App):

    def __init__(self) -> None:
        super().__init__(name="Claude Monitor", title="\u25cf CC", quit_button=None)
        self._cpu_history: list[float] = [0.0] * 8
        # Cached data — updated in background thread
        self._cached_data: dict = {}
        self._cache_lock = __import__("threading").Lock()
        self._slow_tick_count = 0

    @rumps.timer(3)
    def _tick(self, _):
        """Fast tick — only update title from cache, rebuild menu every 3rd tick."""
        try:
            self._fast_update()
            self._slow_tick_count += 1
            if self._slow_tick_count >= 3:  # full menu rebuild every ~9s
                self._slow_tick_count = 0
                # Run heavy data collection in background
                __import__("threading").Thread(target=self._collect_data, daemon=True).start()
        except Exception:
            self.title = "\u25cb CC:err"

    def _collect_data(self) -> None:
        """Background thread — collects all heavy data."""
        try:
            si = get_system_summary()
            sessions_data = load_recent_sessions(20)
            active_ids = get_active_session_ids()
            terms = get_all_terminals_flat()
            tunnels = get_ssh_tunnels()
            external = get_external_connections()
            listeners = get_listening_services()
            cost_data = total_summary(14)
            days = load_costs_by_day(7)

            with self._cache_lock:
                self._cached_data = {
                    "si": si, "sessions": sessions_data,
                    "active_ids": active_ids, "terms": terms,
                    "tunnels": tunnels, "external": external,
                    "listeners": listeners, "costs": cost_data, "days": days,
                }
            # Trigger menu rebuild on main thread
            rumps.Timer(self._rebuild_menu, 0.1).start()
        except Exception:
            pass

    def _rebuild_menu(self, _):
        """Called on main thread after data collection."""
        try:
            with self._cache_lock:
                data = self._cached_data.copy()
            if data:
                self._render_menu(data)
        except Exception:
            pass

    def _fast_update(self) -> None:
        """Fast title-only update from Rust IPC (~5ms)."""
        si = get_system_summary()
        act, cpu = si["active"], si["total_cpu"]
        tot = si["total"]
        self._cpu_history.append(cpu)
        self._cpu_history = self._cpu_history[-8:]
        spark = self._sparkline(self._cpu_history)
        icon = "\u25cf" if act > 0 else "\u25cb"
        self.title = f"{icon} CC:{act}/{tot} {spark} {cpu:.0f}%"

    def _sparkline(self, values: list[float]) -> str:
        """Unicode sparkline from values."""
        chars = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        if not values:
            return ""
        mx = max(values) or 1
        return "".join(chars[min(int(v / mx * 7), 7)] for v in values)

    def _render_menu(self, data: dict) -> None:
        si = data.get("si", {})
        act, idl, tot = si.get("active", 0), si.get("idle", 0), si.get("total", 0)
        cpu, mem = si.get("total_cpu", 0), si.get("total_mem_mb", 0)
        all_p = si.get("all_processes", [])
        by_cat = si.get("by_category", {})
        spark = self._sparkline(self._cpu_history)

        self.menu.clear()
        mx = max((p.cpu_percent for p in all_p), default=1) or 1

        # ━━ Header ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.menu.add(rumps.MenuItem(
            f"\u26a1 AGIMON \u2014 {act} aktiv  {idl} idle  {tot} Claude",
            callback=self._open_tui,
        ))
        self.menu.add(rumps.MenuItem(
            f"CPU {spark} {cpu:.1f}%  \u2502  RAM {mem:.0f}MB  \u2502  {len(all_p)} Prozesse",
            callback=_noop,
        ))
        # Watchdog alerts inline
        try:
            watch_out = subprocess.run(
                [str(Path.home() / ".local/bin/agimon-core"), "watch"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            if "\u26a0" in watch_out:
                lines = [l.strip() for l in watch_out.split("\n") if "\u25cf" in l]
                for alert_line in lines[:3]:
                    clean = alert_line.replace("\x1b[31m\u25cf\x1b[0m ", "")
                    self.menu.add(rumps.MenuItem(f"\u26a0\ufe0f {clean}", callback=_noop))
        except Exception:
            pass
        self.menu.add(None)

        # ━━ Prozesse nach Kategorie ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        CAT_ICONS = {
            "claude": "\U0001f4bb", "dev-tool": "\U0001f527",
            "runtime": "\u2699\ufe0f", "ide": "\U0001f4dd", "infra": "\U0001f433",
        }
        CAT_NAMES = {
            "claude": "Claude Code", "dev-tool": "Dev Tools",
            "runtime": "Runtimes", "ide": "IDEs", "infra": "Infrastruktur",
        }
        for cat in ["claude", "dev-tool", "ide", "runtime", "infra"]:
            procs = by_cat.get(cat, [])
            if not procs:
                continue
            c_cpu = sum(p.cpu_percent for p in procs)
            c_mem = sum(p.mem_mb for p in procs)
            sec = rumps.MenuItem(
                f"{CAT_ICONS.get(cat,'')} {CAT_NAMES.get(cat,cat)} "
                f"({len(procs)})  {c_cpu:.0f}%  {c_mem:.0f}MB",
                callback=_noop,
            )
            for p in procs[:10]:
                bar = _bar(p.cpu_percent, mx, 8)
                ic = "\u25cf" if p.status == "active" else "\u25cb"
                it = rumps.MenuItem(
                    f"{ic} {p.label} {bar} {p.cpu_percent:.1f}%  {p.mem_mb:.0f}MB",
                    callback=_proc_detail(p.pid),
                )
                it.add(rumps.MenuItem(f"\U0001f50d Details + Netzwerk", callback=_proc_detail(p.pid)))
                it.add(rumps.MenuItem(f"\u274c Beenden", callback=_kill_pid(p.pid, p.label)))
                it.add(rumps.MenuItem(f"\U0001f4cb PID {p.pid} kopieren", callback=_copy(str(p.pid))))
                it.add(rumps.MenuItem(f"CMD: {p.command[:45]}", callback=_copy(p.command)))
                sec.add(it)
            self.menu.add(sec)

        self.menu.add(None)

        # ━━ Aktive Sessions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        try:
            sessions = data.get("sessions", [])
            active_ids = data.get("active_ids", set())
            active_sessions = [s for s in sessions if s.session_id in active_ids]
            recent_sessions = [s for s in sessions if s.session_id not in active_ids]

            # Active sessions
            sec = rumps.MenuItem(
                f"\u26a1 Aktive Sessions ({len(active_sessions)})", callback=_noop
            )
            for s in active_sessions:
                ag = f" \u2022 {len(s.subagents)}ag" if s.subagents else ""
                tk = f" \u2022 {fmt_tok(s.input_tokens + s.output_tokens)}"
                msg = (s.first_user_message or "...").replace("\n", " ")[:35]
                it = rumps.MenuItem(
                    f"\u25cf {msg}{ag}{tk}",
                    callback=_resume_session(s.session_id),
                )
                it.add(rumps.MenuItem(
                    f"\u25b6\ufe0f Resume in neuem Fenster",
                    callback=_resume_session(s.session_id),
                ))
                if s.tools_used:
                    tools = ", ".join(f"{t}({c})" for t, c in sorted(s.tools_used.items(), key=lambda x: -x[1])[:4])
                    it.add(rumps.MenuItem(f"Tools: {tools}", callback=_noop))
                for sa in s.subagents[:3]:
                    it.add(rumps.MenuItem(
                        f"\u2514 {sa.agent_id[:10]} [{sa.model or '?'}] {sa.prompt_preview[:30]}",
                        callback=_noop,
                    ))
                it.add(rumps.MenuItem(f"\U0001f4cb ID: {s.session_id[:20]}...", callback=_copy(s.session_id)))
                sec.add(it)
            if not active_sessions:
                sec.add(rumps.MenuItem("  Keine aktiven Sessions", callback=_noop))
            self.menu.add(sec)

            # Recent sessions (resume-fähig)
            sec = rumps.MenuItem(
                f"\U0001f4dc Letzte Sessions ({len(recent_sessions)})", callback=_noop
            )
            for s in recent_sessions[:20]:
                ag = f" {len(s.subagents)}ag" if s.subagents else ""
                ts = s.start_time[5:16] if s.start_time else ""
                msg = (s.first_user_message or "...").replace("\n", " ")[:30]
                it = rumps.MenuItem(
                    f"\u25cb {ts} {msg}{ag}",
                    callback=_resume_session(s.session_id),
                )
                it.add(rumps.MenuItem(
                    f"\u25b6\ufe0f Resume: claude --resume {s.session_id[:12]}...",
                    callback=_resume_session(s.session_id),
                ))
                it.add(rumps.MenuItem(f"\U0001f4cb Session-ID kopieren", callback=_copy(s.session_id)))
                it.add(rumps.MenuItem(
                    f"\U0001f4cb Resume-Command kopieren",
                    callback=_copy(f"claude --resume {s.session_id} --dangerously-skip-permissions"),
                ))
                if s.tools_used:
                    tools = ", ".join(sorted(s.tools_used.keys())[:5])
                    it.add(rumps.MenuItem(f"Tools: {tools}", callback=_noop))
                sec.add(it)
            self.menu.add(sec)
        except Exception:
            self.menu.add(rumps.MenuItem("\U0001f4cb Sessions: Fehler", callback=_noop))

        # ━━ Ghostty Terminals (klickbar = fokussiert) ━━━━━━━━━━━━
        terms = data.get("terms", [])
        sec = rumps.MenuItem(f"\U0001f5a5 Ghostty ({len(terms)} Terminals)", callback=_noop)
        try:
            for t in terms[:15]:
                title = t.get("terminal_title", "")[:28]
                wi = t.get("window_index", 0)
                cwd = t.get("working_dir", "")
                cl = " \u25cf" if "claude" in title.lower() else ""
                it = rumps.MenuItem(
                    f"W{wi} T{t.get('tab_index','?')}: {title}{cl}",
                    callback=_focus_ghost(wi),
                )
                if cwd:
                    it.add(rumps.MenuItem(f"\U0001f4c2 Finder: {cwd}", callback=_open_in_finder(cwd)))
                    it.add(rumps.MenuItem(f"\U0001f4dd Windsurf: {cwd}", callback=_open_in_ide(cwd)))
                    it.add(rumps.MenuItem(f"\U0001f4bb Claude hier", callback=_launch_claude_in(cwd)))
                    it.add(rumps.MenuItem(f"\U0001f4cb CWD kopieren", callback=_copy(cwd)))
                sec.add(it)
        except Exception:
            sec.add(rumps.MenuItem("  Nicht erreichbar", callback=_noop))
        self.menu.add(sec)

        # ━━ Netzwerk ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        sec = rumps.MenuItem("\U0001f310 Netzwerk", callback=_noop)
        tunnels = data.get("tunnels", [])
        external = data.get("external", [])

        sub_t = rumps.MenuItem(f"\U0001f512 SSH Tunnels ({len(tunnels)})", callback=_noop)
        for t in tunnels:
            sub_t.add(rumps.MenuItem(
                f":{t.local_port} \u2192 {t.label or '?'}",
                callback=_open_url(f"http://localhost:{t.local_port}"),
            ))
        sec.add(sub_t)

        pc: dict[str, int] = {}
        for c in external:
            pc[c.process] = pc.get(c.process, 0) + 1
        sub_e = rumps.MenuItem(f"\U0001f30d Extern ({len(external)})", callback=_noop)
        for proc, cnt in sorted(pc.items(), key=lambda x: -x[1])[:8]:
            sub_e.add(rumps.MenuItem(f"{proc}: {cnt}x", callback=_noop))
        sec.add(sub_e)

        non_ssh = [l for l in data.get("listeners", []) if l.process != "ssh"]
        sub_l = rumps.MenuItem(f"\U0001f4e1 Dienste ({len(non_ssh)})", callback=_noop)
        for l in non_ssh[:10]:
            sub_l.add(rumps.MenuItem(
                f"{l.process} :{l.local_port} {l.label or ''}",
                callback=_open_url(f"http://localhost:{l.local_port}"),
            ))
        sec.add(sub_l)
        self.menu.add(sec)

        # ━━ Kosten ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        sec = rumps.MenuItem("\U0001f4b0 Kosten", callback=_noop)
        try:
            sm = data.get("costs", {})
            sec.add(rumps.MenuItem(
                f"14 Tage: ${sm.get('total_cost',0):,.2f}  \u2502  "
                f"{fmt_tok(sm.get('total_tokens',0))} tok  \u2502  "
                f"{sm.get('total_sessions',0)} sess",
                callback=_noop,
            ))
            days = data.get("days", [])
            if days:
                mx_c = max(d.cost for d in days) or 1
                for d in days:
                    b = _bar(d.cost, mx_c, 8)
                    sec.add(rumps.MenuItem(
                        f"{d.date} {b} ${d.cost:>6,.2f}  {d.sessions:>3}s",
                        callback=_noop,
                    ))
        except Exception:
            sec.add(rumps.MenuItem("  Fehler", callback=_noop))
        self.menu.add(sec)

        # ━━ Projekte (smart detected + favorites) ━━━━━━━━━━━━━━━
        self.menu.add(None)
        fav = rumps.MenuItem("\u2b50 Projekte", callback=_noop)
        PROJECTS = [
            ("\U0001f916 SuperJarvis", "/Users/master/projects/SUPERJARVIS", "http://localhost:7777"),
            ("\U0001f4bc SupersynergyCRM", "/Users/master/SupersynergyCRM", "http://localhost:8000"),
            ("\U0001f577 ZeroClaw Agents", "/Users/master/supersynergyapp/supersynergy-agents", None),
            ("\U0001f50d Omni Scraper", "/Users/master/omni-scraper", None),
            ("\u26a1 AGIMON", SCRIPT_DIR, None),
            ("\U0001f4da Plane.so", "/Users/master/plane-docker", "http://localhost:8090"),
        ]
        for name, path, url in PROJECTS:
            if not os.path.isdir(path):
                continue
            pi = rumps.MenuItem(name, callback=_open_in_finder(path))
            pi.add(rumps.MenuItem(f"\U0001f4bb Claude Code starten", callback=_launch_claude_in(path)))
            pi.add(rumps.MenuItem(f"\U0001f4dd In Windsurf \u00f6ffnen", callback=_open_in_ide(path)))
            pi.add(rumps.MenuItem(f"\u2328\ufe0f Terminal hier", callback=_open_in_ghostty(path)))
            pi.add(rumps.MenuItem(f"\U0001f4c2 Im Finder", callback=_open_in_finder(path)))
            if url:
                pi.add(rumps.MenuItem(f"\U0001f310 Web UI \u00f6ffnen", callback=_open_url(url)))
            fav.add(pi)
        self.menu.add(fav)

        # ━━ Quick Links ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        links = rumps.MenuItem("\U0001f517 Quick Links", callback=_noop)
        LINKS = [
            ("\U0001f5a5 AGIMON TUI Dashboard", None, self._open_tui),
            ("\U0001f4ca Qdrant Dashboard", "http://localhost:6333/dashboard", None),
            ("\U0001f916 SuperJarvis :7777", "http://localhost:7777", None),
            ("\U0001f4bc CRM :8000", "http://localhost:8000", None),
            ("\U0001f4cb Plane.so :8090", "http://localhost:8090", None),
            ("\U0001f4ca Grafana :3030", "http://localhost:3030", None),
            ("\U0001f50d Typesense :8108", "http://localhost:8108", None),
            ("\U0001f4e6 Minio :9001", "http://localhost:9001", None),
            ("\U0001f310 Gitea :3000", "http://localhost:3000", None),
            ("\U0001f9e0 Ollama :11434", "http://localhost:11434", None),
        ]
        for label, url, custom_cb in LINKS:
            if custom_cb:
                links.add(rumps.MenuItem(label, callback=custom_cb))
            elif url:
                links.add(rumps.MenuItem(label, callback=_open_url(url)))
        self.menu.add(links)

        self.menu.add(None)
        self.menu.add(rumps.MenuItem("\u274c Alle Claude stoppen", callback=self._stop_all))
        self.menu.add(None)
        self.menu.add(rumps.MenuItem("Beenden", callback=rumps.quit_application))

    def _open_tui(self, _):
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
