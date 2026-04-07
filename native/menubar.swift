// AGIMON Native Menubar — Pure Swift, zero dependencies
// Compile: swiftc -O -o agimon-menu menubar.swift
// ~200KB binary, <5ms startup, native NSStatusBar

import Cocoa

// ── Data Models ─────────────────────────────────────────────────

struct IpcData: Codable {
    let active: Int
    let idle: Int
    let total: Int
    let cpu: Double
    let mem_mb: Int
    let procs: [ProcInfo]
}

struct ProcInfo: Codable {
    let pid: Int
    let label: String
    let cat: String
    let cpu: Double
    let mem: Int
    let s: String
}

struct SessionInfo {
    let id: String
    let message: String
    let agents: Int
    let tokens: String
    let active: Bool
    let tools: String
}

// ── Shell helpers ───────────────────────────────────────────────

func shell(_ cmd: String) -> String {
    let p = Process()
    p.launchPath = "/bin/sh"
    p.arguments = ["-c", cmd]
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    do {
        try p.run()
        p.waitUntilExit()
    } catch { return "" }
    return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
}

func coreBin() -> String {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    return "\(home)/.local/bin/agimon-core"
}

// Cache last successful IPC result — never show "offline" if we had data
private var _lastIpc: IpcData?

func fetchIpc() -> IpcData? {
    let bin = coreBin()
    guard FileManager.default.fileExists(atPath: bin) else { return _lastIpc }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: bin)
    p.arguments = ["ipc"]
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    do {
        try p.run()
        p.waitUntilExit()
    } catch { return _lastIpc }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    if let decoded = try? JSONDecoder().decode(IpcData.self, from: data) {
        _lastIpc = decoded
        return decoded
    }
    return _lastIpc  // fallback to last good data
}

func fetchSessions() -> [SessionInfo] {
    // Parse agimon-core sessions output (colored text)
    let raw = shell("\(coreBin()) sessions 20 2>/dev/null")
    var sessions: [SessionInfo] = []
    // Fallback: parse session JSONLs directly
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    let projDir = "\(home)/.claude/projects"
    guard let dirs = try? FileManager.default.contentsOfDirectory(atPath: projDir) else { return [] }

    for dir in dirs {
        let full = "\(projDir)/\(dir)"
        guard let files = try? FileManager.default.contentsOfDirectory(atPath: full) else { continue }
        let jsonls = files.filter { $0.hasSuffix(".jsonl") }
            .sorted { a, b in
                let ma = (try? FileManager.default.attributesOfItem(atPath: "\(full)/\(a)")[.modificationDate] as? Date) ?? Date.distantPast
                let mb = (try? FileManager.default.attributesOfItem(atPath: "\(full)/\(b)")[.modificationDate] as? Date) ?? Date.distantPast
                return ma > mb
            }
        for f in jsonls.prefix(20) {
            let path = "\(full)/\(f)"
            let sid = String(f.dropLast(6)) // remove .jsonl
            guard let content = try? String(contentsOfFile: path, encoding: .utf8) else { continue }
            let lines = content.components(separatedBy: "\n")
            var firstMsg = ""
            var msgCount = 0

            for line in lines where !line.isEmpty {
                guard let data = line.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                let type = obj["type"] as? String ?? ""
                if type == "user" {
                    msgCount += 1
                    if firstMsg.isEmpty {
                        if let msg = obj["message"] as? [String: Any],
                           let c = msg["content"] as? String {
                            firstMsg = String(c.replacingOccurrences(of: "\n", with: " ").prefix(50))
                        }
                    }
                } else if type == "assistant" { msgCount += 1 }
            }
            if firstMsg.isEmpty { firstMsg = sid.prefix(12) + "..." }

            let mtime = (try? FileManager.default.attributesOfItem(atPath: path)[.modificationDate] as? Date) ?? Date.distantPast
            let isActive = Date().timeIntervalSince(mtime) < 300

            // Count subagents
            let saDir = "\(full)/\(sid)/subagents"
            let saCount = (try? FileManager.default.contentsOfDirectory(atPath: saDir).filter { $0.hasSuffix(".jsonl") }.count) ?? 0

            sessions.append(SessionInfo(
                id: sid, message: firstMsg, agents: saCount,
                tokens: "", active: isActive, tools: ""
            ))
        }
    }
    // Deduplicate by id, sort active first
    var seen = Set<String>()
    var unique: [SessionInfo] = []
    for s in sessions {
        if seen.contains(s.id) { continue }
        seen.insert(s.id)
        unique.append(s)
    }
    return unique.sorted { ($0.active ? 0 : 1) < ($1.active ? 0 : 1) }.prefix(20).map { $0 }
}

// ── Sparkline ───────────────────────────────────────────────────

func sparkline(_ values: [Double]) -> String {
    let chars: [Character] = ["▁","▂","▃","▄","▅","▆","▇","█"]
    let mx = values.max() ?? 1.0
    guard mx > 0 else { return String(repeating: "▁", count: values.count) }
    return String(values.map { chars[min(Int($0 / mx * 7), 7)] })
}

// ── Golden Ratio Color Palette ──────────────────────────────────
// Colors derived from φ (1.618) angle rotation on HSB wheel
// Creates naturally harmonious, sacred-geometry-inspired palette

struct Palette {
    // Vibrant but refined — Figma/Arc/Notion inspired
    // Each category gets a distinct, beautiful color

    // Header — electric blue (Arc browser vibes)
    static let gold     = NSColor(calibratedRed: 0.40, green: 0.65, blue: 1.00, alpha: 1)  // vivid blue
    static let amber    = NSColor(calibratedRed: 0.55, green: 0.75, blue: 1.00, alpha: 1)  // light blue

    // Categories — each visually distinct and beautiful
    static let teal     = NSColor(calibratedRed: 0.30, green: 0.85, blue: 0.75, alpha: 1)  // turquoise
    static let violet   = NSColor(calibratedRed: 0.70, green: 0.50, blue: 0.95, alpha: 1)  // rich purple
    static let sage     = NSColor(calibratedRed: 0.45, green: 0.82, blue: 0.45, alpha: 1)  // fresh green

    // Status — clear and readable
    static let alive    = NSColor(calibratedRed: 0.30, green: 0.85, blue: 0.50, alpha: 1)  // bright green
    static let warn     = NSColor(calibratedRed: 1.00, green: 0.75, blue: 0.25, alpha: 1)  // warm yellow
    static let danger   = NSColor(calibratedRed: 1.00, green: 0.40, blue: 0.40, alpha: 1)  // clear red
    static let muted    = NSColor(calibratedRed: 0.60, green: 0.62, blue: 0.65, alpha: 1)  // readable gray
    static let subtle   = NSColor(calibratedRed: 0.45, green: 0.47, blue: 0.50, alpha: 1)  // dim
    static let text     = NSColor.labelColor
}

// ── Styled menu items ───────────────────────────────────────────

func styledItem(_ title: String, color: NSColor = Palette.text, bold: Bool = false,
                mono: Bool = false, action: Selector? = nil, target: AnyObject? = nil) -> NSMenuItem {
    let item = NSMenuItem()
    let font: NSFont = mono
        ? .monospacedSystemFont(ofSize: 12, weight: bold ? .bold : .regular)
        : .systemFont(ofSize: 13, weight: bold ? .semibold : .regular)
    let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
    item.attributedTitle = NSAttributedString(string: title, attributes: attrs)
    if let action = action { item.action = action }
    if let target = target { item.target = target }
    return item
}

// ── App Delegate ────────────────────────────────────────────────

class AgimonDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var cpuHistory: [Double] = Array(repeating: 0, count: 8)
    var timer: Timer?
    var tickCount = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "⚡ …"

        // Delay first data fetch to not block startup
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            self?.updateTitle(nil)
            self?.rebuildMenu()
        }

        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.onTick()
        }
        RunLoop.current.add(timer!, forMode: .common)
    }

    func onTick() {
        tickCount += 1
        updateTitle(nil)
        if tickCount % 4 == 0 { rebuildMenu() }  // every 20s instead of 15s
    }

    func updateTitle(_ ipc: IpcData?) {
        let data: IpcData
        if let d = ipc ?? fetchIpc() {
            data = d
        } else {
            statusItem.button?.title = "◇ …"
            return
        }

        // Show Claude count in title, not all processes
        let claudeProcs = data.procs.filter { $0.cat == "claude" }
        let claudeActive = claudeProcs.filter { $0.s == "active" }.count
        let claudeTotal = claudeProcs.count
        let icon = claudeTotal > 0 ? "⚡" : "◇"
        statusItem.button?.title = "\(icon) \(claudeActive)/\(claudeTotal)"
    }

    func rebuildMenu() {
        let menu = NSMenu()
        menu.autoenablesItems = false

        let ipc: IpcData
        if let fetched = fetchIpc() {
            ipc = fetched
        } else {
            menu.addItem(styledItem("⚡ AGIMON — wird geladen…", color: Palette.muted, bold: true))
            statusItem.menu = menu
            return
        }

        // ── Header ──
        let claudeCount = ipc.procs.filter { $0.cat == "claude" }.count
        let claudeActive = ipc.procs.filter { $0.cat == "claude" && $0.s == "active" }.count
        menu.addItem(styledItem(
            "⚡ AGIMON — \(claudeActive) aktiv · \(claudeCount) Claude · \(ipc.procs.count) total",
            color: Palette.gold, bold: true
        ))
        let totalMem = ipc.procs.reduce(0) { $0 + $1.mem }
        menu.addItem(styledItem(
            "RAM \(totalMem)MB  │  \(ipc.procs.count) Prozesse",
            color: Palette.muted, mono: true
        ))

        // Watchdog alerts
        let watchOut = shell("\(coreBin()) watch 2>/dev/null")
        if watchOut.contains("⚠") {
            for line in watchOut.components(separatedBy: "\n") where line.contains("●") {
                let clean = line.replacingOccurrences(of: "\u{1b}[31m●\u{1b}[0m ", with: "")
                    .trimmingCharacters(in: .whitespaces)
                menu.addItem(styledItem("⚠️ \(clean)", color: Palette.danger))
            }
        }
        menu.addItem(.separator())

        // ── Processes by category ──
        let cats: [(String, String, NSColor)] = [
            ("claude", "💻 Claude Code", Palette.gold),
            ("dev-tool", "🔧 Dev Tools", Palette.teal),
            ("ide", "📝 IDEs", Palette.violet),
            ("runtime", "⚙️ Runtimes", Palette.muted),
            ("infra", "🐳 Infra", Palette.sage),
        ]
        for (cat, label, color) in cats {
            let catProcs = ipc.procs.filter { $0.cat == cat }
            if catProcs.isEmpty { continue }
            let catCpu = catProcs.reduce(0.0) { $0 + $1.cpu }
            let catMem = catProcs.reduce(0) { $0 + $1.mem }
            let sub = NSMenu()
            for p in catProcs.prefix(12) {
                let icon = p.s == "active" ? "●" : "○"
                let item = styledItem(
                    "\(icon) \(p.label)  \(String(format: "%5.1f", p.cpu))%  \(p.mem)MB  PID:\(p.pid)",
                    color: p.s == "active" ? Palette.alive : Palette.muted, mono: true
                )
                let procSub = NSMenu()
                let infoItem = NSMenuItem(title: "🔍 Details + Netzwerk", action: #selector(showProcessDetail(_:)), keyEquivalent: "")
                infoItem.target = self
                infoItem.representedObject = p.pid
                procSub.addItem(infoItem)
                let killItem = NSMenuItem(title: "❌ Beenden (PID \(p.pid))", action: #selector(killProcess(_:)), keyEquivalent: "")
                killItem.target = self
                killItem.representedObject = p.pid
                procSub.addItem(killItem)
                let copyItem = NSMenuItem(title: "📋 PID kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
                copyItem.target = self
                copyItem.representedObject = "\(p.pid)"
                procSub.addItem(copyItem)
                item.submenu = procSub
                sub.addItem(item)
            }
            let catItem = styledItem(
                "\(label) (\(catProcs.count))  \(String(format: "%.0f", catCpu))%  \(catMem)MB",
                color: color, bold: true
            )
            catItem.submenu = sub
            menu.addItem(catItem)
        }
        menu.addItem(.separator())

        // ── Sessions ──
        let sessions = fetchSessions()
        let activeSessions = sessions.filter { $0.active }
        let recentSessions = sessions.filter { !$0.active }

        if !activeSessions.isEmpty {
            let sec = styledItem("⚡ Aktive Sessions (\(activeSessions.count))", color: Palette.alive, bold: true)
            let sub = NSMenu()
            for s in activeSessions {
                let ag = s.agents > 0 ? " • \(s.agents)ag" : ""
                let item = NSMenuItem(title: "● \(s.message)\(ag)", action: #selector(resumeSession(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = s.id
                sub.addItem(item)
            }
            sec.submenu = sub
            menu.addItem(sec)
        }

        if !recentSessions.isEmpty {
            let sec = styledItem("📜 Letzte Sessions (\(recentSessions.count))", color: Palette.subtle, bold: true)
            let sub = NSMenu()
            for s in recentSessions.prefix(15) {
                let item = NSMenuItem(title: "○ \(s.message)", action: #selector(resumeSession(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = s.id
                let itemSub = NSMenu()
                let resumeItem = NSMenuItem(title: "▶️ Resume in Ghostty", action: #selector(resumeSession(_:)), keyEquivalent: "")
                resumeItem.target = self
                resumeItem.representedObject = s.id
                itemSub.addItem(resumeItem)
                let copyItem = NSMenuItem(title: "📋 Session-ID kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
                copyItem.target = self
                copyItem.representedObject = s.id
                itemSub.addItem(copyItem)
                let cmdItem = NSMenuItem(title: "📋 Resume-CMD kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
                cmdItem.target = self
                cmdItem.representedObject = "/Users/master/.local/bin/claude --resume \(s.id) --dangerously-skip-permissions"
                itemSub.addItem(cmdItem)
                item.submenu = itemSub
                sub.addItem(item)
            }
            sec.submenu = sub
            menu.addItem(sec)
        }
        menu.addItem(.separator())

        // ── Quick Links ──
        let links: [(String, String)] = [
            ("🖥 TUI Dashboard", "tui"),
            ("📊 Qdrant", "http://localhost:6333/dashboard"),
            ("🤖 SuperJarvis", "http://localhost:7777"),
            ("📋 Plane.so", "http://localhost:8090"),
            ("🌐 Gitea", "http://localhost:3000"),
            ("📊 Grafana", "http://localhost:3030"),
        ]
        let linkSec = styledItem("🔗 Quick Links", bold: true)
        let linkSub = NSMenu()
        for (label, url) in links {
            if url == "tui" {
                let item = NSMenuItem(title: label, action: #selector(openTui), keyEquivalent: "")
                item.target = self
                linkSub.addItem(item)
            } else {
                let item = NSMenuItem(title: label, action: #selector(openUrl(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = url
                linkSub.addItem(item)
            }
        }
        linkSec.submenu = linkSub
        menu.addItem(linkSec)

        // ── Projects ──
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let projects: [(String, String, String?)] = [
            ("🤖 SuperJarvis", "\(home)/projects/SUPERJARVIS", "http://localhost:7777"),
            ("💼 SupersynergyCRM", "\(home)/SupersynergyCRM", "http://localhost:8000"),
            ("🕷 ZeroClaw", "\(home)/supersynergyapp/supersynergy-agents", nil),
            ("🔍 Omni Scraper", "\(home)/omni-scraper", nil),
            ("⚡ AGIMON", "\(home)/claude-monitor", nil),
        ]
        let projSec = styledItem("⭐ Projekte", bold: true)
        let projSub = NSMenu()
        for (name, path, webUrl) in projects {
            guard FileManager.default.fileExists(atPath: path) else { continue }
            let item = NSMenuItem(title: name, action: #selector(openInFinder(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = path
            let sub2 = NSMenu()

            let claude = NSMenuItem(title: "💻 Claude Code starten", action: #selector(launchClaude(_:)), keyEquivalent: "")
            claude.target = self; claude.representedObject = path
            sub2.addItem(claude)

            let ide = NSMenuItem(title: "📝 In Windsurf", action: #selector(openInIde(_:)), keyEquivalent: "")
            ide.target = self; ide.representedObject = path
            sub2.addItem(ide)

            let term = NSMenuItem(title: "⌨️ Terminal", action: #selector(openTerminal(_:)), keyEquivalent: "")
            term.target = self; term.representedObject = path
            sub2.addItem(term)

            let finder = NSMenuItem(title: "📂 Im Finder", action: #selector(openInFinder(_:)), keyEquivalent: "")
            finder.target = self; finder.representedObject = path
            sub2.addItem(finder)

            if let url = webUrl {
                let web = NSMenuItem(title: "🌐 Web UI", action: #selector(openUrl(_:)), keyEquivalent: "")
                web.target = self; web.representedObject = url
                sub2.addItem(web)
            }
            item.submenu = sub2
            projSub.addItem(item)
        }
        projSec.submenu = projSub
        menu.addItem(projSec)

        menu.addItem(.separator())
        let killAll = NSMenuItem(title: "❌ Alle Claude stoppen", action: #selector(killAllClaude), keyEquivalent: "")
        killAll.target = self
        menu.addItem(killAll)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Beenden", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quit)

        statusItem.menu = menu
    }

    // ── Actions ─────────────────────────────────────────────────

    @objc func showProcessDetail(_ sender: NSMenuItem) {
        guard let pid = sender.representedObject as? Int else { return }
        let info = shell("ps -p \(pid) -o pid,ppid,%cpu,%mem,rss,etime,command 2>/dev/null")
        let net = shell("lsof -i -nP -a -p \(pid) 2>/dev/null | head -8")
        let alert = NSAlert()
        alert.messageText = "Prozess \(pid)"
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Kill")
        alert.addButton(withTitle: "PID kopieren")
        alert.addButton(withTitle: "Schließen")
        alert.icon = NSImage(named: NSImage.advancedName)

        let text = NSMutableAttributedString()
        let mono = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        text.append(NSAttributedString(string: "── Prozess ──\n", attributes: [.font: mono, .foregroundColor: Palette.gold]))
        text.append(NSAttributedString(string: info + "\n\n", attributes: [.font: mono, .foregroundColor: NSColor.labelColor]))
        text.append(NSAttributedString(string: "── Netzwerk ──\n", attributes: [.font: mono, .foregroundColor: Palette.gold]))
        text.append(NSAttributedString(string: net.isEmpty ? "Keine Verbindungen" : net, attributes: [.font: mono, .foregroundColor: Palette.teal]))

        let tv = NSTextField(frame: NSRect(x: 0, y: 0, width: 500, height: 200))
        tv.attributedStringValue = text
        tv.isEditable = false; tv.isBezeled = false; tv.drawsBackground = false; tv.isSelectable = true
        alert.accessoryView = tv

        let result = alert.runModal()
        if result == .alertFirstButtonReturn {
            shell("kill \(pid)")
        } else if result == .alertSecondButtonReturn {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString("\(pid)", forType: .string)
        }
    }

    @objc func killProcess(_ sender: NSMenuItem) {
        guard let pid = sender.representedObject as? Int else { return }
        let alert = NSAlert()
        alert.messageText = "PID \(pid) beenden?"
        alert.addButton(withTitle: "Kill")
        alert.addButton(withTitle: "Abbrechen")
        alert.icon = NSImage(named: NSImage.cautionName)
        if alert.runModal() == .alertFirstButtonReturn {
            shell("kill \(pid)")
        }
    }

    @objc func copyText(_ sender: NSMenuItem) {
        guard let text = sender.representedObject as? String else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    @objc func resumeSession(_ sender: NSMenuItem) {
        guard let sid = sender.representedObject as? String else { return }
        shell("""
            osascript -e 'tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set command of cfg to "/Users/master/.local/bin/claude --resume \(sid) --dangerously-skip-permissions"
                new window with configuration cfg
            end tell'
        """)
    }

    @objc func openTui() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let dir = "\(home)/claude-monitor"
        shell("""
            osascript -e 'tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set command of cfg to "\(dir)/.venv/bin/python3 \(dir)/app.py"
                set initial working directory of cfg to "\(dir)"
                new window with configuration cfg
            end tell'
        """)
    }

    @objc func openUrl(_ sender: NSMenuItem) {
        guard let url = sender.representedObject as? String else { return }
        NSWorkspace.shared.open(URL(string: url)!)
    }

    @objc func openInFinder(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    @objc func openInIde(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        shell("windsurf '\(path)' 2>/dev/null || open -a 'Visual Studio Code' '\(path)'")
    }

    @objc func openTerminal(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        shell("""
            osascript -e 'tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "\(path)"
                new window with configuration cfg
            end tell'
        """)
    }

    @objc func launchClaude(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        shell("""
            osascript -e 'tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set initial working directory of cfg to "\(path)"
                set command of cfg to "/Users/master/.local/bin/claude --dangerously-skip-permissions"
                new window with configuration cfg
            end tell'
        """)
    }

    @objc func killAllClaude() {
        let alert = NSAlert()
        alert.messageText = "Alle Claude-Instanzen stoppen?"
        alert.addButton(withTitle: "Kill All")
        alert.addButton(withTitle: "Abbrechen")
        if alert.runModal() == .alertFirstButtonReturn {
            shell("pkill -f 'claude.*--dangerously'")
        }
    }
}

// ── Main ────────────────────────────────────────────────────────

let app = NSApplication.shared
app.setActivationPolicy(.accessory) // no dock icon
let delegate = AgimonDelegate()
app.delegate = delegate
app.run()
