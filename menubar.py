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

    @rumps.timer(4)
    def _tick(self, _):
        try: self._render()
        except Exception: self.title = "\u25cb CC:err"

    def _render(self) -> None:
        si = get_system_summary()
        act, idl, tot = si["active"], si["idle"], si["total"]
        cpu, mem = si["total_cpu"], si["total_mem_mb"]
        all_p = si.get("all_processes", [])
        by_cat = si.get("by_category", {})

        icon = "\u25cf" if act > 0 else "\u25cb"
        self.title = f"{icon} CC:{act}/{tot}  {cpu:.0f}%  {mem:.0f}MB"

        self.menu.clear()
        mx = max((p.cpu_percent for p in all_p), default=1) or 1

        # ━━ Header ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.menu.add(rumps.MenuItem(
            f"\u26a1 Claude Code \u2014 {act} aktiv  {idl} idle  {tot} total",
            callback=self._open_tui,
        ))
        self.menu.add(rumps.MenuItem(
            f"\u03a3 CPU {cpu:.1f}%  \u2502  RAM {mem:.0f}MB  \u2502  {len(all_p)} Prozesse",
            callback=_noop,
        ))
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

        # ━━ Sessions mit Quick-Actions ━━━━━━━━━━━━━━━━━━━━━━━━━━━
        sec = rumps.MenuItem("\U0001f4cb Sessions", callback=_noop)
        try:
            sessions = load_recent_sessions(8)
            active_ids = get_active_session_ids()
            for s in sessions:
                is_act = s.session_id in active_ids
                ic = "\u25cf" if is_act else "\u25cb"
                ag = f" \u2022 {len(s.subagents)}ag" if s.subagents else ""
                tk = f" \u2022 {fmt_tok(s.input_tokens + s.output_tokens)}"
                msg = (s.first_user_message or "...").replace("\n", " ")[:38]
                it = rumps.MenuItem(f"{ic} {msg}{ag}{tk}", callback=_copy(s.first_user_message[:200]))

                # Quick-action: open project in various ways
                if s.project:
                    proj_path = os.path.expanduser(
                        s.project.replace("-Users-master", "/Users/master")
                            .replace("-", "/", 2) if s.project.startswith("-") else s.project
                    )
                    if os.path.isdir(proj_path):
                        it.add(rumps.MenuItem(f"\U0001f4c2 Im Finder \u00f6ffnen", callback=_open_in_finder(proj_path)))
                        it.add(rumps.MenuItem(f"\U0001f4dd In Windsurf \u00f6ffnen", callback=_open_in_ide(proj_path)))
                        it.add(rumps.MenuItem(f"\U0001f4bb Claude hier starten", callback=_launch_claude_in(proj_path)))
                        it.add(rumps.MenuItem(f"\u2328\ufe0f Terminal hier", callback=_open_in_ghostty(proj_path)))

                if s.tools_used:
                    tools = ", ".join(f"{t}({c})" for t, c in sorted(s.tools_used.items(), key=lambda x: -x[1])[:4])
                    it.add(rumps.MenuItem(f"Tools: {tools}", callback=_noop))
                for sa in s.subagents[:2]:
                    it.add(rumps.MenuItem(
                        f"\u2514 {sa.agent_id[:10]} [{sa.model or '?'}] {sa.prompt_preview[:25]}",
                        callback=_noop,
                    ))
                it.add(rumps.MenuItem(f"\U0001f4cb Session-ID kopieren", callback=_copy(s.session_id)))
                sec.add(it)
        except Exception:
            sec.add(rumps.MenuItem("  Fehler", callback=_noop))
        self.menu.add(sec)

        # ━━ Ghostty Terminals (klickbar = fokussiert) ━━━━━━━━━━━━
        sec = rumps.MenuItem(f"\U0001f5a5 Ghostty ({len(get_all_terminals_flat())} Terminals)", callback=_noop)
        try:
            terms = get_all_terminals_flat()
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
        tunnels = get_ssh_tunnels()
        external = get_external_connections()

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

        non_ssh = [l for l in get_listening_services() if l.process != "ssh"]
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
            sm = total_summary(14)
            sec.add(rumps.MenuItem(
                f"14 Tage: ${sm['total_cost']:,.2f}  \u2502  "
                f"{fmt_tok(sm['total_tokens'])} tok  \u2502  "
                f"{sm['total_sessions']} sess",
                callback=_noop,
            ))
            days = load_costs_by_day(7)
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

        # ━━ Quick Actions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.menu.add(None)
        self.menu.add(rumps.MenuItem("\U0001f5a5 Dashboard (TUI)", callback=self._open_tui))
        self.menu.add(rumps.MenuItem("\U0001f4ca Qdrant Dashboard", callback=_open_url("http://localhost:6333/dashboard")))
        self.menu.add(rumps.MenuItem("\U0001f916 SuperJarvis", callback=_open_url("http://localhost:7777")))
        self.menu.add(rumps.MenuItem("\U0001f4c1 ~/claude-monitor", callback=_open_in_finder(SCRIPT_DIR)))
        self.menu.add(None)

        # Frequent project launchers
        fav = rumps.MenuItem("\u2b50 Projekte", callback=_noop)
        PROJECTS = [
            ("SupersynergyCRM", "/Users/master/SupersynergyCRM"),
            ("ZeroClaw Agents", "/Users/master/supersynergyapp/supersynergy-agents"),
            ("Omni Scraper", "/Users/master/omni-scraper"),
            ("Claude Monitor", SCRIPT_DIR),
        ]
        for name, path in PROJECTS:
            if os.path.isdir(path):
                pi = rumps.MenuItem(f"\U0001f4c2 {name}", callback=_open_in_finder(path))
                pi.add(rumps.MenuItem(f"\U0001f4bb Claude starten", callback=_launch_claude_in(path)))
                pi.add(rumps.MenuItem(f"\U0001f4dd In Windsurf", callback=_open_in_ide(path)))
                pi.add(rumps.MenuItem(f"\u2328\ufe0f Terminal", callback=_open_in_ghostty(path)))
                pi.add(rumps.MenuItem(f"\U0001f4c2 Im Finder", callback=_open_in_finder(path)))
                fav.add(pi)
        self.menu.add(fav)

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
