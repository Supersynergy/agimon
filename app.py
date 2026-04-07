"""Claude Monitor — Universal Dashboard for Claude Code + Ghostty + Network."""
from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)

from collectors.costs import load_costs_by_day, total_summary
from collectors.processes import get_claude_processes, get_system_summary
from collectors.ghostty import get_all_terminals_flat, focus_terminal
from collectors.sessions import load_recent_sessions, get_active_session_ids
from collectors.network import get_network_summary
from collectors.windows import get_all_windows, focus_window
from collectors.qdrant_store import search_sessions, get_collection_stats

# ── Helpers ──────────────────────────────────────────────────────────


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_cost(c: float) -> str:
    return f"${c:,.2f}"


def make_bar(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return ""
    filled = int((value / max_val) * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


# ── Summary Banner ───────────────────────────────────────────────────


class SummaryBanner(Static):
    """Top summary bar with aggregate stats."""

    def render_stats(self, summary: dict, sys_info: dict) -> str:
        days = summary["days"]
        return (
            f"Letzte {days} Tage: "
            f"{fmt_tokens(summary['total_tokens'])} Tokens  |  "
            f"{fmt_cost(summary['total_cost'])}  |  "
            f"{summary['total_sessions']} Sessions  |  "
            f"{summary['total_messages']} Msgs"
        )


class StatusBar(Static):
    """Bottom status bar with live process info."""

    def render_status(self, sys_info: dict, qdrant_stats: dict) -> str:
        return (
            f"\u03a3 {sys_info['total']} Prozess  |  "
            f"{sys_info['active']} aktiv  |  "
            f"{sys_info['idle']} idle  |  "
            f"CPU {sys_info['total_cpu']}%  |  "
            f"RAM {sys_info['total_mem_mb']:.0f}MB  |  "
            f"Qdrant: {qdrant_stats['points']} pts"
        )


# ── Live Tab ─────────────────────────────────────────────────────────


class LiveView(VerticalScroll):
    """Real-time process + Ghostty mapping."""

    def compose(self) -> ComposeResult:
        yield DataTable(id="live-table")

    def on_mount(self) -> None:
        table = self.query_one("#live-table", DataTable)
        table.add_columns(
            "PID", "TTY", "Status", "CPU%", "RAM MB",
            "Gestartet", "Ghostty Window", "CWD"
        )
        table.cursor_type = "row"
        table.zebra_stripes = True

    def refresh_data(self, terminals: list[dict] | None = None) -> None:
        table = self.query_one("#live-table", DataTable)
        table.clear()

        procs = get_claude_processes()
        if terminals is None:
            terminals = []

        for proc in procs:
            ghostty_win = ""
            cwd = ""
            for term in terminals:
                t_title = term.get("terminal_title", "")
                t_cwd = term.get("working_dir", "")
                if "claude" in t_title.lower() or str(proc.pid) in t_title:
                    ghostty_win = (
                        f"W{term.get('window_index', '?')}:"
                        f"{term.get('window_title', '?')[:12]} "
                        f"T{term.get('tab_index', '?')}"
                    )
                    cwd = t_cwd[:30]
                    break

            status_icon = "\u25cf" if proc.status == "active" else "\u25cb"
            table.add_row(
                str(proc.pid),
                proc.tty,
                f"{status_icon} {proc.status}",
                f"{proc.cpu_percent:.1f}",
                f"{proc.mem_mb:.0f}",
                proc.started,
                ghostty_win or "\u2014",
                cwd or "\u2014",
            )


# ── Instanzen Tab ────────────────────────────────────────────────────


class InstanceView(VerticalScroll):
    """Session instances with subagent details."""

    def compose(self) -> ComposeResult:
        yield DataTable(id="instance-table")
        yield Static("", id="instance-detail")

    def on_mount(self) -> None:
        table = self.query_one("#instance-table", DataTable)
        table.add_columns(
            "", "Session", "Aufgabe", "Tokens In", "Tokens Out",
            "Tools", "Agents", "Letzte Aktivit\u00e4t"
        )
        table.cursor_type = "row"
        table.zebra_stripes = True

    def refresh_data(self) -> None:
        table = self.query_one("#instance-table", DataTable)
        detail = self.query_one("#instance-detail", Static)
        table.clear()

        sessions = load_recent_sessions(limit=20)
        active_ids = get_active_session_ids()

        detail_lines: list[str] = []
        for s in sessions:
            is_active = s.session_id in active_ids
            status = "\u25cf" if is_active else "\u25cb"
            top_tools = ", ".join(
                f"{t}({c})" for t, c in
                sorted(s.tools_used.items(), key=lambda x: -x[1])[:3]
            ) if s.tools_used else "\u2014"

            table.add_row(
                status,
                s.session_id[:12] + "...",
                (s.first_user_message or "\u2014")[:40],
                fmt_tokens(s.input_tokens),
                fmt_tokens(s.output_tokens),
                top_tools[:30],
                str(len(s.subagents)),
                s.last_activity[11:19] if s.last_activity else "\u2014",
            )

            if is_active and s.subagents:
                detail_lines.append(
                    f"\n\u2500\u2500 Session {s.session_id[:12]} "
                    f"\u2500 {len(s.subagents)} Subagents:"
                )
                for sa in s.subagents:
                    sa_tools = ", ".join(t.tool_name for t in sa.tool_uses[:5])
                    detail_lines.append(
                        f"   \u251c {sa.agent_id[:16]} "
                        f"[{sa.model or '?'}] "
                        f"\u2014 {sa.prompt_preview[:60]}"
                    )
                    if sa_tools:
                        detail_lines.append(f"   \u2502   Tools: {sa_tools}")
                    detail_lines.append(
                        f"   \u2502   Tokens: "
                        f"{fmt_tokens(sa.input_tokens)} in / "
                        f"{fmt_tokens(sa.output_tokens)} out / "
                        f"{sa.message_count} msgs"
                    )

        detail.update(
            "\n".join(detail_lines) if detail_lines
            else "\n  Keine aktiven Subagents"
        )


# ── Statistik Tab ────────────────────────────────────────────────────


class StatsView(VerticalScroll):
    """Daily cost/token statistics with bar charts."""

    def compose(self) -> ComposeResult:
        yield DataTable(id="stats-table")

    def on_mount(self) -> None:
        table = self.query_one("#stats-table", DataTable)
        table.add_columns(
            "Datum", "          ", "Kosten", "Tokens",
            "In", "Out", "Sessions", "Msgs"
        )
        table.cursor_type = "row"
        table.zebra_stripes = True

    def refresh_data(self) -> None:
        table = self.query_one("#stats-table", DataTable)
        table.clear()

        stats = load_costs_by_day(days=14)
        if not stats:
            return

        max_cost = max((d.cost for d in stats), default=1)
        for day in stats:
            bar = make_bar(day.cost, max_cost, 18)
            table.add_row(
                day.date, bar, fmt_cost(day.cost),
                fmt_tokens(day.total_tokens),
                fmt_tokens(day.input_tokens),
                fmt_tokens(day.output_tokens),
                str(day.sessions), str(day.messages),
            )


# ── Netzwerk Tab ─────────────────────────────────────────────────────


class NetworkView(VerticalScroll):
    """SSH tunnels, listening services, external connections."""

    def compose(self) -> ComposeResult:
        yield Static("[bold]SSH Tunnels[/bold]", id="net-tunnels-label")
        yield DataTable(id="net-tunnels")
        yield Static("[bold]Lauschende Dienste[/bold]", id="net-listen-label")
        yield DataTable(id="net-listen")
        yield Static("[bold]Externe Verbindungen[/bold]", id="net-ext-label")
        yield DataTable(id="net-external")

    def on_mount(self) -> None:
        t1 = self.query_one("#net-tunnels", DataTable)
        t1.add_columns("Port", "Adresse", "PID", "Label")
        t1.cursor_type = "row"
        t1.zebra_stripes = True

        t2 = self.query_one("#net-listen", DataTable)
        t2.add_columns("Prozess", "Port", "Adresse", "PID", "Label")
        t2.cursor_type = "row"
        t2.zebra_stripes = True

        t3 = self.query_one("#net-external", DataTable)
        t3.add_columns("Prozess", "Remote", "Port", "PID", "Label")
        t3.cursor_type = "row"
        t3.zebra_stripes = True

    def refresh_data(self, net_data: dict | None = None) -> None:
        if net_data is None:
            net_data = get_network_summary()

        t1 = self.query_one("#net-tunnels", DataTable)
        t1.clear()
        for c in net_data["tunnels"]:
            t1.add_row(
                str(c.local_port), c.local_addr,
                str(c.pid), c.label,
            )

        t2 = self.query_one("#net-listen", DataTable)
        t2.clear()
        for c in net_data["listeners"]:
            if c.process == "ssh":
                continue  # shown in tunnels
            t2.add_row(
                c.process, str(c.local_port), c.local_addr,
                str(c.pid), c.label,
            )

        t3 = self.query_one("#net-external", DataTable)
        t3.clear()
        for c in net_data["external"]:
            t3.add_row(
                c.process, c.remote_addr,
                str(c.remote_port), str(c.pid), c.label,
            )


# ── Ghostty Tab ──────────────────────────────────────────────────────


class GhosttyView(VerticalScroll):
    """Ghostty window/tab/terminal mapping with focus."""

    def compose(self) -> ComposeResult:
        yield Static(
            "Enter = Fenster fokussieren", id="ghostty-hint"
        )
        yield DataTable(id="ghostty-table")

    def on_mount(self) -> None:
        table = self.query_one("#ghostty-table", DataTable)
        table.add_columns(
            "W#", "Window", "Tab#", "Terminal",
            "Working Dir", "Claude?"
        )
        table.cursor_type = "row"
        table.zebra_stripes = True

    def refresh_data(self, terminals: list[dict] | None = None) -> None:
        table = self.query_one("#ghostty-table", DataTable)
        table.clear()
        if terminals is None:
            return
        for t in terminals:
            title = t.get("terminal_title", "")
            is_claude = "\u25cf" if "claude" in title.lower() else ""
            table.add_row(
                str(t.get("window_index", "")),
                t.get("window_title", "")[:25],
                str(t.get("tab_index", "")),
                title[:30],
                t.get("working_dir", "")[:35],
                is_claude,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Focus the selected Ghostty window."""
        table = self.query_one("#ghostty-table", DataTable)
        row = table.get_row(event.row_key)
        if row:
            try:
                win_idx = int(row[0])
                focus_terminal(win_idx)
            except (ValueError, IndexError):
                pass


# ── Fenster Tab (Global) ─────────────────────────────────────────────


class WindowsView(VerticalScroll):
    """Global window search across all apps + focus."""

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="Fenster suchen... (Enter = fokussieren)",
            id="win-search",
        )
        yield DataTable(id="win-table")

    def on_mount(self) -> None:
        table = self.query_one("#win-table", DataTable)
        table.add_columns("App", "W#", "Fenster-Titel", "Position", "Size")
        table.cursor_type = "row"
        table.zebra_stripes = True

    def refresh_data(self, windows_data: list | None = None) -> None:
        table = self.query_one("#win-table", DataTable)
        table.clear()
        if windows_data is None:
            return
        for w in windows_data:
            table.add_row(
                w.app_name[:15],
                str(w.window_index),
                w.window_title[:40],
                w.position[:15],
                w.size[:15],
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "win-search":
            return
        query = event.value.strip()
        if not query:
            return
        self._search_and_show(query)

    @work(thread=True)
    def _search_and_show(self, query: str) -> None:
        from collectors.windows import search_windows
        results = search_windows(query)
        table = self.query_one("#win-table", DataTable)
        self.app.call_from_thread(table.clear)
        for w in results:
            self.app.call_from_thread(
                table.add_row,
                w.app_name[:15], str(w.window_index),
                w.window_title[:40], w.position[:15], w.size[:15],
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Focus the selected window."""
        table = self.query_one("#win-table", DataTable)
        row = table.get_row(event.row_key)
        if row:
            try:
                app_name = row[0].strip()
                win_idx = int(row[1])
                focus_window(app_name, win_idx)
            except (ValueError, IndexError):
                pass


# ── Suche Tab ────────────────────────────────────────────────────────


class SearchView(VerticalScroll):
    """Qdrant-powered semantic search over session history."""

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="Semantic search \u00fcber Sessions...",
            id="search-input",
        )
        yield DataTable(id="search-results")
        yield Static("", id="search-status")

    def on_mount(self) -> None:
        table = self.query_one("#search-results", DataTable)
        table.add_columns("Score", "Session", "Text", "Zeitpunkt")
        table.cursor_type = "row"
        table.zebra_stripes = True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search-input":
            return
        query = event.value.strip()
        if not query:
            return
        self._do_search(query)

    @work(thread=True)
    def _do_search(self, query: str) -> None:
        status = self.query_one("#search-status", Static)
        table = self.query_one("#search-results", DataTable)

        self.app.call_from_thread(status.update, "Suche l\u00e4uft...")
        results = search_sessions(query, limit=15)
        self.app.call_from_thread(table.clear)

        if not results:
            self.app.call_from_thread(
                status.update,
                "Keine Ergebnisse. (Qdrant 'claude_monitor' leer?)"
            )
            return

        for r in results:
            self.app.call_from_thread(
                table.add_row,
                f"{r.get('score', 0):.3f}",
                r.get("session_id", "?")[:12],
                r.get("text", "")[:60],
                r.get("timestamp", "")[:19],
            )
        self.app.call_from_thread(
            status.update, f"{len(results)} Ergebnisse"
        )


# ── Main App ─────────────────────────────────────────────────────────

STYLESHEET = """
Screen {
    background: #1a1a1a;
}
Header {
    background: #2a2a2a;
    color: #ff9500;
}
Footer {
    background: #2a2a2a;
}
SummaryBanner {
    dock: top;
    height: 1;
    background: #2a2a2a;
    color: #8a8a8a;
    padding: 0 1;
}
StatusBar {
    dock: bottom;
    height: 1;
    background: #2a2a2a;
    color: #8a8a8a;
    padding: 0 1;
}
TabbedContent {
    height: 1fr;
}
ContentSwitcher {
    height: 1fr;
}
TabPane {
    height: 1fr;
    padding: 0;
}
DataTable {
    height: 1fr;
}
DataTable > .datatable--header {
    background: #333333;
    color: #ff9500;
    text-style: bold;
}
DataTable > .datatable--cursor {
    background: #3a3a3a;
    color: #ffffff;
}
DataTable > .datatable--even-row {
    background: #222222;
}
DataTable > .datatable--odd-row {
    background: #1a1a1a;
}
#instance-detail {
    height: auto;
    max-height: 15;
    background: #1e1e1e;
    color: #cccccc;
    padding: 0 1;
    border-top: solid #333333;
}
#search-input, #win-search {
    dock: top;
    margin: 0 0 1 0;
    background: #2a2a2a;
    color: #ffffff;
    border: solid #555555;
}
#search-status {
    height: 1;
    color: #8a8a8a;
    padding: 0 1;
}
#ghostty-hint {
    height: 1;
    color: #666666;
    padding: 0 1;
}
#net-tunnels-label, #net-listen-label, #net-ext-label {
    height: 1;
    color: #ff9500;
    padding: 0 1;
    margin-top: 1;
}
#net-tunnels, #net-listen, #net-external {
    height: auto;
    max-height: 12;
}
"""


class ClaudeMonitor(App):
    """Claude Code Activity Monitor."""

    CSS = STYLESHEET
    TITLE = "Claude Monitor"
    SUB_TITLE = ""
    BINDINGS = [
        Binding("q", "quit", "Beenden"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "tab_live", "Live", priority=True),
        Binding("2", "tab_instances", "Instanzen", priority=True),
        Binding("3", "tab_stats", "Statistik", priority=True),
        Binding("4", "tab_network", "Netzwerk", priority=True),
        Binding("5", "tab_ghostty", "Ghostty", priority=True),
        Binding("6", "tab_windows", "Fenster", priority=True),
        Binding("7", "tab_search", "Suche", priority=True),
    ]

    _refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBanner(id="summary")
        with TabbedContent(id="tabs"):
            with TabPane("Live", id="tab-live"):
                yield LiveView(id="live-view")
            with TabPane("Instanzen", id="tab-instances"):
                yield InstanceView(id="instance-view")
            with TabPane("Statistik", id="tab-stats"):
                yield StatsView(id="stats-view")
            with TabPane("Netzwerk", id="tab-network"):
                yield NetworkView(id="network-view")
            with TabPane("Ghostty", id="tab-ghostty"):
                yield GhosttyView(id="ghostty-view")
            with TabPane("Fenster", id="tab-windows"):
                yield WindowsView(id="windows-view")
            with TabPane("Suche", id="tab-search"):
                yield SearchView(id="search-view")
        yield StatusBar(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()
        self._refresh_timer = self.set_interval(5, self.action_refresh)

    @work(thread=True)
    def action_refresh(self) -> None:
        """Refresh all data — all I/O happens in this worker thread."""
        # Gather all data in the worker thread (no main-thread blocking)
        summary = total_summary(14)
        sys_info = get_system_summary()
        qdrant_stats = get_collection_stats()

        try:
            terminals = get_all_terminals_flat()
        except Exception:
            terminals = []

        # Update header
        self.call_from_thread(
            setattr, self, "sub_title",
            f"{sys_info['active']} arbeiten, {sys_info['idle']} idle"
        )

        # Update banner
        try:
            banner = self.query_one("#summary", SummaryBanner)
            text = banner.render_stats(summary, sys_info)
            self.call_from_thread(banner.update, text)
        except NoMatches:
            pass

        # Update status bar
        try:
            status = self.query_one("#status", StatusBar)
            text = status.render_status(sys_info, qdrant_stats)
            self.call_from_thread(status.update, text)
        except NoMatches:
            pass

        # Refresh active tab only
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            active = tabs.active

            if active == "tab-live":
                view = self.query_one("#live-view", LiveView)
                self.call_from_thread(view.refresh_data, terminals)
            elif active == "tab-instances":
                view = self.query_one("#instance-view", InstanceView)
                self.call_from_thread(view.refresh_data)
            elif active == "tab-stats":
                view = self.query_one("#stats-view", StatsView)
                self.call_from_thread(view.refresh_data)
            elif active == "tab-network":
                net_data = get_network_summary()
                view = self.query_one("#network-view", NetworkView)
                self.call_from_thread(view.refresh_data, net_data)
            elif active == "tab-ghostty":
                view = self.query_one("#ghostty-view", GhosttyView)
                self.call_from_thread(view.refresh_data, terminals)
            elif active == "tab-windows":
                wins = get_all_windows()
                view = self.query_one("#windows-view", WindowsView)
                self.call_from_thread(view.refresh_data, wins)
        except NoMatches:
            pass

    def action_tab_live(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-live"

    def action_tab_instances(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-instances"

    def action_tab_stats(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-stats"

    def action_tab_network(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-network"

    def action_tab_ghostty(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-ghostty"

    def action_tab_windows(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-windows"

    def action_tab_search(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-search"


if __name__ == "__main__":
    ClaudeMonitor().run()
