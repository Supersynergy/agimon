// AGIMON Native Menubar — Pure Swift, Asynchronous Architecture
// Powered by agimon-core menu-data (100x faster than Python)
// Compile: swiftc -O -o agimon-menu menubar.swift

import Cocoa

// ── Data Models ─────────────────────────────────────────────────

struct MenuData: Codable {
    struct Proc: Codable {
        let pid: Int
        let label: String
        let cat: String
        let cpu: Double
        let mem: Int
        let s: String
    }
    struct Budget: Codable {
        let spent: Double
        let budget: Double
        let remaining: Double
        let alerts: [String]
        let at_risk: Int
    }
    struct Mlx: Codable {
        let available: Bool
        let count: Int
        let models: [String]
    }
    let procs: [Proc]
    let watchdog: [String]
    let budget: Budget
    let mlx: Mlx
}

struct SessionInfo {
    let id: String
    let message: String     // first user message preview
    let agents: Int
    let tokens: String
    let active: Bool        // modified < 10min ago
    let tools: String
    let cwd: String         // working directory
    let modDate: Date       // last modified
    let msgCount: Int       // total user messages
    let isRunning: Bool     // actually running as process right now
    let runningPid: String  // PID if running
    
    var projectName: String {
        guard !cwd.isEmpty else { return "" }
        return URL(fileURLWithPath: cwd).lastPathComponent
    }
    
    var timeAgo: String {
        let secs = Int(Date().timeIntervalSince(modDate))
        if secs < 60 { return "\(secs)s" }
        if secs < 3600 { return "\(secs/60)m" }
        if secs < 86400 { return "\(secs/3600)h" }
        return "\(secs/86400)d"
    }
}

// ── Claude Code Usage ───────────────────────────────────────────

struct ClaudeUsage {
    let todayCost: Double
    let todayTokens: Int
    let yesterdayCost: Double
    let weekCost: Double
    let weekTokens: Int
    let allTimeCost: Double
    let days: Int                          // total days of usage
    let modelsToday: [String]              // model names used today
    let plan: String                       // "Max" / "Pro" / "API" / "?"
    let dailyHistory: [(date: String, cost: Double, tokens: Int)] // last 30 days
    let monthCost: Double                  // current month
    let cacheReadTokens: Int               // today cache reads
    let cacheCreationTokens: Int           // today cache writes
}

func fetchClaudeUsage() -> ClaudeUsage {
    let home = NSHomeDirectory()
    let ccusageBin = "\(home)/.bun/bin/ccusage"
    let cacheFile = "\(home)/.claude/stats/agimon-usage-cache.json"
    let todayStr2: String = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f.string(from: Date())
    }()

    // ── 1. Check agimon daily cache (ccusage takes 18s, cache daily) ───
    var allDays: [[String: Any]] = []
    var needsRebuild = true

    if let cacheData = FileManager.default.contents(atPath: cacheFile),
       let cacheObj = try? JSONSerialization.jsonObject(with: cacheData) as? [String: Any],
       let builtDate = cacheObj["builtDate"] as? String,
       builtDate == todayStr2,
       let days = cacheObj["daily"] as? [[String: Any]] {
        allDays = days
        needsRebuild = false
    }

    if needsRebuild {
        // Check if cache exists but is from yesterday — use it temporarily while rebuilding
        if allDays.isEmpty,
           let staleData = FileManager.default.contents(atPath: cacheFile),
           let staleObj = try? JSONSerialization.jsonObject(with: staleData) as? [String: Any],
           let staleDays = staleObj["daily"] as? [[String: Any]] {
            allDays = staleDays  // Use stale data immediately, rebuild async
        }

        // Launch ccusage rebuild in a fire-and-forget detached process (writes cache)
        // This avoids blocking for 18s on every fetch
        let rebuildScript = """
        \(ccusageBin) daily --json --offline 2>/dev/null | \
        python3 -c "
import sys, json, datetime
data = json.load(sys.stdin)
days = data.get('daily', [])
out = {'builtDate': datetime.date.today().strftime('%Y-%m-%d'), 'daily': days}
with open('\(cacheFile)', 'w') as f:
    json.dump(out, f)
" &
"""
        let rp = Process()
        rp.launchPath = "/bin/sh"
        rp.arguments = ["-c", rebuildScript]
        rp.standardOutput = FileHandle.nullDevice
        rp.standardError = FileHandle.nullDevice
        try? rp.run()
        // Don't wait — fire and forget
    }

    let _ymd: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f }()
    let todayStr = _ymd.string(from: Date())
    let cal = Calendar.current
    let yesterdayStr = _ymd.string(from: cal.date(byAdding: .day, value: -1, to: Date())!)
    let weekAgo = cal.date(byAdding: .day, value: -7, to: Date())!
    let monthStart = cal.date(from: cal.dateComponents([.year, .month], from: Date()))!

    var todayCost = 0.0, todayTokens = 0, yesterdayCost = 0.0
    var weekCost = 0.0, weekTokens = 0, allTimeCost = 0.0, monthCost = 0.0
    var modelsToday: [String] = []
    var cacheRead = 0, cacheCreate = 0
    var history: [(date: String, cost: Double, tokens: Int)] = []

    let df = DateFormatter()
    df.dateFormat = "yyyy-MM-dd"

    for day in allDays {
        let dateStr = day["date"] as? String ?? ""
        let cost    = day["totalCost"] as? Double ?? 0
        let tokens  = day["totalTokens"] as? Int ?? 0
        allTimeCost += cost
        history.append((date: String(dateStr.suffix(5)), cost: cost, tokens: tokens))

        if dateStr == todayStr {
            todayCost   = cost
            todayTokens = tokens
            cacheRead   = day["cacheReadTokens"] as? Int ?? 0
            cacheCreate = day["cacheCreationTokens"] as? Int ?? 0
            if let bk = day["modelBreakdowns"] as? [[String: Any]] {
                modelsToday = bk.compactMap { $0["modelName"] as? String }
                    .map { $0.replacingOccurrences(of: "claude-", with: "")
                             .replacingOccurrences(of: "-20\\d\\d\\d\\d\\d\\d", with: "", options: .regularExpression) }
            }
        }
        if dateStr == yesterdayStr { yesterdayCost = cost }
        if let d = df.date(from: dateStr) {
            if d >= weekAgo  { weekCost += cost; weekTokens += tokens }
            if d >= monthStart { monthCost += cost }
        }
    }

    // ── 2. Always override today from today-summary.json (live, fast) ─
    // This keeps the current day accurate even when ccusage cache is from earlier today.
    let summaryPath = "\(home)/.claude/stats/today-summary.json"
    if let sumData = FileManager.default.contents(atPath: summaryPath),
       let sumObj = try? JSONSerialization.jsonObject(with: sumData) as? [String: Any] {
        let liveTok = sumObj["todayTokens"] as? Int ?? 0
        if liveTok > todayTokens { todayTokens = liveTok }  // always use highest token count
        // If ccusage returned nothing at all, use summary for everything
        if allDays.isEmpty {
            todayCost     = sumObj["today"]      as? Double ?? 0
            yesterdayCost = sumObj["yesterday"]  as? Double ?? 0
            weekCost      = sumObj["week"]       as? Double ?? 0
            weekTokens    = sumObj["weekTokens"] as? Int    ?? 0
            allTimeCost   = sumObj["allTime"]    as? Double ?? 0
        }
    }

    // ── 3. Plan detection via Keychain OAuth ───────────────────────
    var plan = "Max"
    let settingsPath = "\(home)/.claude/settings.json"
    if let data = FileManager.default.contents(atPath: settingsPath),
       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
        let hasApiKey = (obj["primaryApiKey"] as? String)?.isEmpty == false ||
                        (obj["apiKey"] as? String)?.isEmpty == false
        if hasApiKey { plan = "API" }
    }

    return ClaudeUsage(
        todayCost: todayCost, todayTokens: todayTokens,
        yesterdayCost: yesterdayCost,
        weekCost: weekCost, weekTokens: weekTokens,
        allTimeCost: allTimeCost,
        days: allDays.count,
        modelsToday: modelsToday,
        plan: plan,
        dailyHistory: Array(history.suffix(30)),
        monthCost: monthCost,
        cacheReadTokens: cacheRead,
        cacheCreationTokens: cacheCreate
    )
}

// ── macOS Notification helper ────────────────────────────────────
func sendNotification(title: String, body: String) {
    let script = "display notification \"\(body)\" with title \"\(title)\""
    var err: NSDictionary?
    NSAppleScript(source: script)?.executeAndReturnError(&err)
}

// ── Claude Rate-Limit (Live API) ────────────────────────────────

struct ClaudeRateLimit {
    // utilization = percentage 0..100 (or >100 if over limit)
    let fiveHour: Double          // 5-hour rolling window %
    let fiveHourResetsIn: Int     // minutes until reset
    let sevenDay: Double          // 7-day rolling window %
    let sevenDayResetsIn: Int     // hours until reset
    let sevenDaySonnet: Double?   // sonnet-specific 7-day limit %
    let extraUsed: Double?        // extra credits used $
    let extraLimit: Double?       // extra credits monthly limit $
    let extraPct: Double?         // extra credits %
    let plan: String              // "Max" / "Pro" / "API" from API response
    let cacheAge: Int             // seconds since last API call
}

func fetchClaudeRateLimit() -> ClaudeRateLimit? {
    let home = NSHomeDirectory()

    // 1. Try claude-hud cache first (fresh enough = <90s)
    let cachePath = "\(home)/.claude/plugins/claude-hud/.usage-cache.json"
    var cacheAge = Int.max
    if let cacheData = FileManager.default.contents(atPath: cachePath),
       let cacheObj = try? JSONSerialization.jsonObject(with: cacheData) as? [String: Any] {
        let ts = cacheObj["timestamp"] as? Double ?? 0
        cacheAge = Int((Date().timeIntervalSince1970 * 1000 - ts) / 1000)

        // Use cache if fresh enough (<= 300s), prefer lastGoodData
        let cachePayload = (cacheObj["data"] as? [String: Any])
            .flatMap { $0.isEmpty ? nil : $0 } ?? cacheObj["lastGoodData"] as? [String: Any]
        if cacheAge <= 300, let d = cachePayload {
            return parseHudCache(d, cacheAge: cacheAge)
        }
    }

    // 2. Get OAuth token from Keychain
    let p = Process()
    p.launchPath = "/usr/bin/security"
    p.arguments = ["find-generic-password", "-s", "Claude Code-credentials", "-w"]
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    guard (try? p.run()) != nil else { return nil }
    p.waitUntilExit()
    let rawToken = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    let tokenJson = rawToken.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !tokenJson.isEmpty,
          let tokenData = tokenJson.data(using: .utf8),
          let tokenObj = try? JSONSerialization.jsonObject(with: tokenData) as? [String: Any],
          let oauthObj = tokenObj["claudeAiOauth"] as? [String: Any],
          let accessToken = oauthObj["accessToken"] as? String,
          !accessToken.isEmpty else {
        // Fall back to stale cache
        if let cacheData = FileManager.default.contents(atPath: cachePath),
           let cacheObj = try? JSONSerialization.jsonObject(with: cacheData) as? [String: Any],
           let d = (cacheObj["lastGoodData"] ?? cacheObj["data"]) as? [String: Any] {
            return parseHudCache(d, cacheAge: cacheAge)
        }
        return nil
    }

    // 3. Call Anthropic OAuth usage API
    let apiResult = shell("""
    curl -s --max-time 5 'https://api.anthropic.com/api/oauth/usage' \\
      -H 'Authorization: Bearer \(accessToken)' \\
      -H 'anthropic-version: 2023-06-01' \\
      -H 'anthropic-beta: oauth-2025-04-20'
    """)
    guard !apiResult.isEmpty,
          let apiData = apiResult.data(using: .utf8),
          let api = try? JSONSerialization.jsonObject(with: apiData) as? [String: Any],
          api["error"] == nil else {
        // API failed or rate-limited — use stale cache (any age)
        if let cacheData = FileManager.default.contents(atPath: cachePath),
           let cacheObj = try? JSONSerialization.jsonObject(with: cacheData) as? [String: Any] {
            let d = (cacheObj["lastGoodData"] as? [String: Any])
                 ?? (cacheObj["data"] as? [String: Any])
            if let d = d { return parseHudCache(d, cacheAge: cacheAge) }
        }
        return nil
    }

    func pct(_ key: String) -> Double? {
        (api[key] as? [String: Any])?["utilization"] as? Double
    }
    func resetMins(_ key: String) -> Int {
        guard let resetsAt = (api[key] as? [String: Any])?["resets_at"] as? String else { return 0 }
        let fmt = ISO8601DateFormatter()
        fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = fmt.date(from: resetsAt) else { return 0 }
        return max(0, Int(date.timeIntervalSinceNow / 60))
    }

    let fh = pct("five_hour") ?? 0
    let sd = pct("seven_day") ?? 0
    let sdSon = pct("seven_day_sonnet")
    let extra = api["extra_usage"] as? [String: Any]
    let extraUsed = extra?["used_credits"] as? Double
    let extraLimit = extra?["monthly_limit"] as? Double
    let extraPct = extra?["utilization"] as? Double

    return ClaudeRateLimit(
        fiveHour: fh,
        fiveHourResetsIn: resetMins("five_hour"),
        sevenDay: sd,
        sevenDayResetsIn: resetMins("seven_day") / 60,
        sevenDaySonnet: sdSon,
        extraUsed: extraUsed,
        extraLimit: extraLimit,
        extraPct: extraPct,
        plan: "Max",
        cacheAge: 0
    )
}

private func parseHudCache(_ d: [String: Any], cacheAge: Int) -> ClaudeRateLimit {
    let fh = d["fiveHour"] as? Double ?? 0
    let sd = d["sevenDay"] as? Double ?? 0
    let plan = d["planName"] as? String ?? "Max"

    func minsUntil(_ key: String) -> Int {
        guard let s = d[key] as? String else { return 0 }
        let fmt = ISO8601DateFormatter()
        fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = fmt.date(from: s) else { return 0 }
        return max(0, Int(date.timeIntervalSinceNow / 60))
    }

    return ClaudeRateLimit(
        fiveHour: fh,
        fiveHourResetsIn: minsUntil("fiveHourResetAt"),
        sevenDay: sd,
        sevenDayResetsIn: minsUntil("sevenDayResetAt") / 60,
        sevenDaySonnet: nil,
        extraUsed: nil, extraLimit: nil, extraPct: nil,
        plan: plan,
        cacheAge: cacheAge
    )
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
    let home = NSHomeDirectory()
    return "\(home)/.local/bin/agimon-core"
}

// ── Data Fetching (RUNS IN BACKGROUND) ──────────────────────────

func fetchMenuData() -> MenuData? {
    let bin = coreBin()
    guard FileManager.default.fileExists(atPath: bin) else { return nil }
    let raw = shell("\(bin) menu-data 2>/dev/null")
    if let data = raw.data(using: String.Encoding.utf8) {
        return try? JSONDecoder().decode(MenuData.self, from: data)
    }
    return nil
}

// ── Running claude process detection ────────────────────────────

struct RunningClaude {
    let pid: String
    let sessionId: String  // from -r flag, or "" if new session
    let tty: String        // e.g. "ttys007"
}

struct GhosttyWindow {
    let windowIndex: Int   // 1-based index for AppleScript "item N of every window"
    let tty: String        // e.g. "ttys007"
    let sessionId: String  // claude session ID or ""
    let pid: String
    let isNew: Bool        // claude --dangerously-skip-permissions (no -r)
    let bufferText: String // last ~200 chars of terminal buffer (for content search)

    var label: String {
        if !sessionId.isEmpty {
            // Find human-readable project name instead of ID
            return "👻 Session: \(sessionId.prefix(8))…"
        }
        if !pid.isEmpty { return "🆕 Neue Session (PID:\(pid))" }
        return "💻 Terminal @ \(tty)"
    }
}

// ═════════════════════════════════════════════════════════════════
// Agent-Desktop Inspired Features (Screenshot, Clipboard, Wait)
// ═════════════════════════════════════════════════════════════════

// Screenshot: Use macOS screencapture command (modern replacement for deprecated CGWindowListCreateImage)
func captureScreenshot(windowId: CGWindowID? = nil, appName: String? = nil) -> NSImage? {
    let tempPath = "/tmp/agimon_screenshot_\(Int(Date().timeIntervalSince1970)).png"

    if let app = appName {
        // Capture specific app using screencapture
        shell("screencapture -l $(osascript -e 'tell app \"\(app)\" to id of front window' 2>/dev/null || echo '') \"​\(tempPath)\" 2>/dev/null || screencapture -x \"​\(tempPath)\" 2>/dev/null")
    } else {
        // Capture entire screen
        shell("screencapture -x \"​\(tempPath)\" 2>/dev/null")
    }

    // Load and return image
    return NSImage(contentsOfFile: tempPath)
}

// Save screenshot to file
func saveScreenshot(_ image: NSImage, to path: String) -> Bool {
    guard let tiffData = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiffData),
          let pngData = bitmap.representation(using: .png, properties: [:]) else {
        return false
    }

    let url = URL(fileURLWithPath: path)
    do {
        try pngData.write(to: url)
        return true
    } catch {
        return false
    }
}

// ═════════════════════════════════════════════════════════════════
// Clipboard Extensions (Get/Set/Clear)
// ═════════════════════════════════════════════════════════════════

func clipboardGet() -> String {
    return NSPasteboard.general.string(forType: .string) ?? ""
}

func clipboardSet(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}

func clipboardClear() {
    NSPasteboard.general.clearContents()
}

// ═════════════════════════════════════════════════════════════════
// Wait Mechanisms (Window, Text, Timeout)
// ═════════════════════════════════════════════════════════════════

enum WaitCondition {
    case window(title: String, app: String?)
    case text(content: String, app: String?)
    case time(milliseconds: Int)
}

// ═════════════════════════════════════════════════════════════════
// Universal Window Management (ALL Apps, ALL Spaces)
// ═════════════════════════════════════════════════════════════════

struct ManagedWindow {
    let windowId: CGWindowID
    let pid: pid_t
    let appName: String
    let appBundleId: String
    let title: String
    let frame: CGRect
    let isOnScreen: Bool
    let workspaceId: Int  // Space/Desktop ID (if available)
    var humanLabel: String { "\(appIcon) \(appName): \(shortTitle)" }

    var appIcon: String {
        switch appName.lowercased() {
        case let s where s.contains("ghostty"): return "👻"
        case let s where s.contains("code"): return "📝"
        case let s where s.contains("chrome"), let s where s.contains("safari"): return "🌐"
        case let s where s.contains("slack"): return "💬"
        case let s where s.contains("terminal"), let s where s.contains("iterm"): return "💻"
        case let s where s.contains("finder"): return "📁"
        case let s where s.contains("music"), let s where s.contains("spotify"): return "🎵"
        case let s where s.contains("mail"), let s where s.contains("outlook"): return "📧"
        case let s where s.contains("calendar"): return "📅"
        case let s where s.contains("notes"), let s where s.contains("bear"): return "📝"
        default: return "🪟"
        }
    }

    var shortTitle: String {
        let max = 40
        if title.count > max {
            return String(title.prefix(max)) + "…"
        }
        return title
    }
}

// Get ALL windows across ALL apps and spaces using CoreGraphics
func getAllManagedWindows() -> [ManagedWindow] {
    let options: CGWindowListOption = [.optionAll, .excludeDesktopElements]
    guard let windowList = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
        return []
    }

    var windows: [ManagedWindow] = []
    for win in windowList {
        guard let windowId = win[kCGWindowNumber as String] as? CGWindowID,
              let pid = win[kCGWindowOwnerPID as String] as? pid_t,
              let appName = win[kCGWindowOwnerName as String] as? String,
              let title = win[kCGWindowName as String] as? String else {
            continue
        }

        // Skip system apps and invisible windows
        if appName.lowercased().hasPrefix("window server") { continue }
        if appName.lowercased() == "loginwindow" { continue }
        if appName.lowercased() == "agimon" { continue }

        // Get window bounds
        var frame = CGRect.zero
        if let bounds = win[kCGWindowBounds as String] as? [String: CGFloat],
           let x = bounds["X"], let y = bounds["Y"],
           let w = bounds["Width"], let h = bounds["Height"] {
            frame = CGRect(x: x, y: y, width: w, height: h)
        }

        let isOnScreen = win[kCGWindowIsOnscreen as String] as? Bool ?? false
        let layer = win[kCGWindowLayer as String] as? Int ?? 0
        guard layer == 0 else { continue }  // Only normal windows, not menus/popups

        // Get bundle ID using NSWorkspace
        let runningApps = NSWorkspace.shared.runningApplications
        let runningApp = runningApps.first { $0.processIdentifier == pid }
        let bundleId = runningApp?.bundleIdentifier ?? ""

        windows.append(ManagedWindow(
            windowId: windowId,
            pid: pid,
            appName: appName,
            appBundleId: bundleId,
            title: title,
            frame: frame,
            isOnScreen: isOnScreen,
            workspaceId: 0  // TODO: Private API needed for exact space ID
        ))
    }

    return windows.sorted { $0.appName < $1.appName }
}

// Get windows grouped by app
func getWindowsByApp() -> [(app: String, windows: [ManagedWindow])] {
    let all = getAllManagedWindows()
    let grouped = Dictionary(grouping: all) { $0.appName }
    return grouped.map { (app: $0.key, windows: $0.value) }.sorted { $0.app < $1.app }
}

// Universal grid layout for ANY app's windows
func arrangeAppWindows(_ appName: String, layout: GridLayout, padding: Int = 8) {
    guard let screen = NSScreen.main else { return }
    let screenFrame = screen.visibleFrame
    let (cols, rows) = layout.dimensions

    // Build AppleScript for this specific app
    let script = """
tell application "\(appName)" to activate
delay 0.2
tell application "System Events"
    tell process "\(appName)"
        set winCount to count of windows
        if winCount = 0 then return "No windows"

        set screenW to \(Int(screenFrame.width))
        set screenH to \(Int(screenFrame.height))
        set originX to \(Int(screenFrame.minX))
        set originY to \(Int(screenFrame.minY))
        set pad to \(padding)
        set cols to \(cols)
        set rows to \(rows)
        set winW to (screenW - (pad * (cols + 1))) / cols
        set winH to (screenH - (pad * (rows + 1))) / rows

        repeat with i from 1 to winCount
            if i > (cols * rows) then exit repeat
            set col to (i - 1) mod cols
            set row to rows - 1 - ((i - 1) div cols)  -- Invert: top row first
            set x to originX + pad + (col * (winW + pad))
            set y to originY + pad + (row * (winH + pad))
            try
                set position of window i to {x, y}
                set size of window i to {winW, winH}
            end try
        end repeat
        return "Arranged " & winCount & " windows"
    end tell
end tell
"""

    var err: NSDictionary?
    NSAppleScript(source: script)?.executeAndReturnError(&err)
}

// Cascade windows (stacked with offset)
func cascadeAppWindows(_ appName: String, offset: Int = 30) {
    let script = """
tell application "\(appName)" to activate
delay 0.2
tell application "System Events"
    tell process "\(appName)"
        set winCount to count of windows
        set baseX to 100
        set baseY to 100
        set off to \(offset)
        repeat with i from 1 to winCount
            try
                set position of window i to {baseX + ((i - 1) * off), baseY + ((i - 1) * off)}
            end try
        end repeat
    end tell
end tell
"""
    var err: NSDictionary?
    NSAppleScript(source: script)?.executeAndReturnError(&err)
}

// Move all windows of an app to current space
func gatherAppWindows(_ appName: String) {
    // Activate app to bring windows to current space
    let script = """
tell application "\(appName)" to activate
delay 0.1
tell application "System Events"
    tell process "\(appName)"
        repeat with w in every window
            try
                perform action "AXRaise" of w
            end try
        end repeat
    end tell
end tell
"""
    var err: NSDictionary?
    NSAppleScript(source: script)?.executeAndReturnError(&err)
}

func getRunningClaudeSessions() -> [RunningClaude] {
    // ps -eo pid,tty,args: get PID + TTY + command
    let raw = shell("ps -eo pid,tty,args 2>/dev/null | grep -E 'claude' | grep -v grep | grep -v 'Claude\\.app'")
    var result: [RunningClaude] = []
    for line in raw.split(separator: "\n") {
        let parts = line.trimmingCharacters(in: .whitespaces).components(separatedBy: " ").filter { !$0.isEmpty }
        guard parts.count >= 3 else { continue }
        let pid = parts[0]
        let tty = parts[1]  // e.g. "ttys007" or "??"
        var sid = ""
        for (i, p) in parts.enumerated() {
            if (p == "-r" || p == "--resume") && i + 1 < parts.count {
                sid = parts[i + 1]
                break
            }
        }
        result.append(RunningClaude(pid: pid, sessionId: sid, tty: tty))
    }
    return result
}

// Maps TTY opening order → Ghostty window index (newest tty = window 1)
// Also reads terminal buffer text via Accessibility API for content search
func getGhosttyWindows() -> [GhosttyWindow] {
    // "who" gives login sessions sorted by login time
    let whoRaw = shell("who 2>/dev/null | grep ttys | sort -k3,4 | awk '{print $2}'")
    let ttysOrdered = whoRaw.split(separator: "\n").map(String.init)  // oldest first
    let totalWindows = ttysOrdered.count
    guard totalWindows > 0 else { return [] }

    // Build TTY → claude info map
    let running = getRunningClaudeSessions()
    var ttyToSession: [String: RunningClaude] = [:]
    for r in running where r.tty != "??" {
        ttyToSession[r.tty] = r
    }

    // Read terminal buffer text for each Ghostty window via Accessibility API
    // Script returns tab-separated: windowIndex\tbufferSnippet (last 500 chars)
    let axScript = """
    set output to ""
    tell application "System Events"
        tell process "Ghostty"
            set wins to every window
            set wCount to count of wins
            repeat with i from 1 to wCount
                set txt to ""
                try
                    set allElems to entire contents of item i of wins
                    repeat with e in allElems
                        try
                            if class of e is text area then
                                set raw to value of e as text
                                if length of raw > 500 then
                                    set txt to text ((length of raw) - 499) thru (length of raw) of raw
                                else
                                    set txt to raw
                                end if
                                exit repeat
                            end if
                        end try
                    end repeat
                end try
                set output to output & i & "\t" & txt & "\n---WIN---\n"
            end repeat
        end tell
    end tell
    return output
    """
    var bufferByIndex: [Int: String] = [:]
    var err: NSDictionary?
    if let result = NSAppleScript(source: axScript)?.executeAndReturnError(&err),
       let raw = result.stringValue {
        for chunk in raw.components(separatedBy: "\n---WIN---\n") where !chunk.isEmpty {
            if let nl = chunk.firstIndex(of: "\t") {
                let idxStr = String(chunk[chunk.startIndex..<nl])
                let text = String(chunk[chunk.index(after: nl)...])
                if let idx = Int(idxStr) { bufferByIndex[idx] = text }
            }
        }
    }

    // TTY order: oldest = index N (last), newest = index 1
    var windows: [GhosttyWindow] = []
    for (i, tty) in ttysOrdered.enumerated() {
        let winIdx = totalWindows - i  // oldest → last index, newest → index 1
        let r = ttyToSession[tty]
        windows.append(GhosttyWindow(
            windowIndex: winIdx,
            tty: tty,
            sessionId: r?.sessionId ?? "",
            pid: r?.pid ?? "",
            isNew: r != nil && (r?.sessionId.isEmpty ?? true),
            bufferText: bufferByIndex[winIdx] ?? ""
        ))
    }
    return windows.sorted { $0.windowIndex < $1.windowIndex }
}

// Focus a specific Ghostty window by index (1-based)
func focusGhosttyWindow(_ index: Int) {
    let script = """
tell application "Ghostty" to activate
delay 0.1
tell application "System Events"
    tell process "Ghostty"
        perform action "AXRaise" of item \(index) of (every window)
    end tell
end tell
"""
    var err: NSDictionary?
    NSAppleScript(source: script)?.executeAndReturnError(&err)
}

// ═════════════════════════════════════════════════════════════════
// Ghostty Window Grid Alignment (2x2, 3x3, 4x4, Custom)
// ═════════════════════════════════════════════════════════════════

enum GridLayout {
    case twoByOne, twoByTwo, threeByTwo, threeByThree, fourByFour, custom(cols: Int, rows: Int)

    var description: String {
        switch self {
        case .twoByOne: return "2×1 Split"
        case .twoByTwo: return "2×2 Grid"
        case .threeByTwo: return "3×2 Grid"
        case .threeByThree: return "3×3 Grid"
        case .fourByFour: return "4×4 Grid"
        case .custom(let c, let r): return "\(c)×\(r) Custom"
        }
    }

    var dimensions: (cols: Int, rows: Int) {
        switch self {
        case .twoByOne: return (2, 1)
        case .twoByTwo: return (2, 2)
        case .threeByTwo: return (3, 2)
        case .threeByThree: return (3, 3)
        case .fourByFour: return (4, 4)
        case .custom(let c, let r): return (c, r)
        }
    }
}

func arrangeGhosttyWindows(_ layout: GridLayout, padding: Int = 8) {
    // Get screen dimensions (main screen)
    guard let screen = NSScreen.main else { return }
    let screenFrame = screen.visibleFrame  // Excludes menu bar/dock
    let screenW = Int(screenFrame.width)
    let screenH = Int(screenFrame.height)
    let originX = Int(screenFrame.minX)
    let originY = Int(screenFrame.minY)

    let (cols, rows) = layout.dimensions
    let count = cols * rows

    // Calculate window size (minus padding)
    let winW = (screenW - (padding * (cols + 1))) / cols
    let winH = (screenH - (padding * (rows + 1))) / rows

    // Build AppleScript to arrange windows using 'window i' syntax (tested working)
    var positionScript = """
tell application "Ghostty" to activate
delay 0.1
tell application "System Events"
    tell process "Ghostty"
        set winCount to count of windows
"""

    // Position each window in the grid
    for i in 0..<count {
        let col = i % cols
        let row = i / cols  // 0 is top row
        let x = originX + padding + (col * (winW + padding))
        let y = originY + padding + ((rows - 1 - row) * (winH + padding))  // Invert row (top to bottom)

        // AppleScript is 1-indexed for windows - use 'window i' not 'item i'
        let winIndex = i + 1
        positionScript += """
        if winCount >= \(winIndex) then
            try
                set position of window \(winIndex) to {\(x), \(y)}
                set size of window \(winIndex) to {\(winW), \(winH)}
            on error
                -- Window might be minimized or hidden, skip
            end try
        end if
"""
    }

    positionScript += """
    end tell
end tell
"""

    var err: NSDictionary?
    NSAppleScript(source: positionScript)?.executeAndReturnError(&err)
}

// Update Ghostty window titles with project names (called from class where lastSessions is available)
func updateGhosttyWindowTitles(lastSessions: [SessionInfo]) {
    // Get current Ghostty windows with their session info
    let windows = getGhosttyWindows()

    for gw in windows where !gw.sessionId.isEmpty {
        // Find project name for this session
        if let session = lastSessions.first(where: { $0.id == gw.sessionId }) {
            let project = session.projectName.isEmpty ? "untitled" : session.projectName
            let titleScript = """
tell application "System Events"
    tell process "Ghostty"
        try
            set name of window \(gw.windowIndex) to "👻 \(project) · \(gw.sessionId.prefix(8))…"
        end try
    end tell
end tell
"""
            var err: NSDictionary?
            NSAppleScript(source: titleScript)?.executeAndReturnError(&err)
        }
    }
}

// ── Native Swift JSONL parser ────────────────────────────────────

func fetchSessionsNative() -> [SessionInfo] {
    let fm = FileManager.default
    let home = NSHomeDirectory()
    let projectsDir = URL(fileURLWithPath: "\(home)/.claude/projects")

    // Get running sessions first (fast)
    let running = getRunningClaudeSessions()
    var runningBySid: [String: RunningClaude] = [:]  // sid -> RunningClaude
    for r in running where !r.sessionId.isEmpty {
        runningBySid[r.sessionId] = r
    }

    guard let projectDirs = try? fm.contentsOfDirectory(
        at: projectsDir, includingPropertiesForKeys: [.contentModificationDateKey],
        options: [.skipsHiddenFiles]
    ) else { return [] }

    // Collect .jsonl files sorted by modification date — only stat, don't read yet
    var jsonlFiles: [(URL, Date)] = []
    for dir in projectDirs {
        guard let files = try? fm.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.contentModificationDateKey], options: []
        ) else { continue }
        for f in files where f.pathExtension == "jsonl" {
            guard !f.deletingLastPathComponent().lastPathComponent.hasPrefix(".") else { continue }
            let mod = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            jsonlFiles.append((f, mod))
        }
    }
    jsonlFiles.sort { $0.1 > $1.1 }

    var sessions: [SessionInfo] = []
    let cutoff = Date().addingTimeInterval(-7 * 24 * 3600)

    // Fast tail-read: read first 3KB for firstUserMsg/cwd, last 12KB for msgCount
    // Avoids loading 298k JSONL lines into memory
    func readTailLines(_ url: URL, maxBytes: Int) -> [String] {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return [] }
        defer { try? fh.close() }
        let size = (try? fh.seekToEnd()) ?? 0
        let offset = size > UInt64(maxBytes) ? size - UInt64(maxBytes) : 0
        try? fh.seek(toOffset: offset)
        let data = fh.readDataToEndOfFile()
        guard let str = String(data: data, encoding: .utf8) else { return [] }
        var lines = str.components(separatedBy: "\n").filter { !$0.isEmpty }
        if offset > 0 { lines.removeFirst() }  // first line may be partial
        return lines
    }

    func readHeadLines(_ url: URL, maxBytes: Int) -> [String] {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return [] }
        defer { try? fh.close() }
        let data = fh.readData(ofLength: maxBytes)
        guard let str = String(data: data, encoding: .utf8) else { return [] }
        return str.components(separatedBy: "\n").filter { !$0.isEmpty }
    }

    func parseUserText(_ obj: [String: Any]) -> String {
        guard let msg = obj["message"] as? [String: Any] else { return "" }
        let c = msg["content"]
        if let s = c as? String { return s }
        if let arr = c as? [[String: Any]] {
            return arr.compactMap { $0["text"] as? String }.joined(separator: " ")
        }
        return ""
    }

    for (file, modDate) in jsonlFiles.prefix(50) {
        guard modDate > cutoff else { continue }

        let sessionId = file.deletingPathExtension().lastPathComponent
        var firstUserMsg = ""
        var cwd = ""
        var msgCount = 0
        var agentCount = 0

        // ── Head: grab firstUserMsg + cwd (first 3KB is enough)
        for line in readHeadLines(file, maxBytes: 3072) {
            guard let data = line.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let type_ = obj["type"] as? String else { continue }
            if type_ == "user" {
                if cwd.isEmpty, let c = obj["cwd"] as? String { cwd = c }
                if firstUserMsg.isEmpty {
                    let text = parseUserText(obj).trimmingCharacters(in: .whitespacesAndNewlines)
                        .replacingOccurrences(of: "\n", with: " ")
                    if !text.hasPrefix("⚠") && !text.hasPrefix("<") && text.count > 3 {
                        firstUserMsg = String(text.prefix(60))
                    }
                }
                if !cwd.isEmpty && !firstUserMsg.isEmpty { break }
            }
        }

        // ── Tail: count messages (last 12KB covers hundreds of recent msgs)
        for line in readTailLines(file, maxBytes: 12288) {
            guard let data = line.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let type_ = obj["type"] as? String else { continue }
            if type_ == "user" {
                msgCount += 1
                // Also catch cwd/msg if head read missed (very short file)
                if cwd.isEmpty, let c = obj["cwd"] as? String { cwd = c }
                if firstUserMsg.isEmpty {
                    let text = parseUserText(obj).trimmingCharacters(in: .whitespacesAndNewlines)
                        .replacingOccurrences(of: "\n", with: " ")
                    if !text.hasPrefix("⚠") && !text.hasPrefix("<") && text.count > 3 {
                        firstUserMsg = String(text.prefix(60))
                    }
                }
            }
            if type_ == "system", let ut = obj["userType"] as? String, ut == "agent" {
                agentCount += 1
            }
        }

        let rc = runningBySid[sessionId]
        let pid = rc?.pid ?? ""
        let isRunning = !pid.isEmpty
        let isActive = isRunning || Date().timeIntervalSince(modDate) < 600
        let display = firstUserMsg.isEmpty ? "[\(sessionId.prefix(12))]" : firstUserMsg

        sessions.append(SessionInfo(
            id: sessionId, message: display, agents: agentCount,
            tokens: "", active: isActive, tools: "",
            cwd: cwd, modDate: modDate, msgCount: msgCount,
            isRunning: isRunning, runningPid: pid
        ))
    }

    return sessions.sorted {
        if $0.isRunning != $1.isRunning { return $0.isRunning }
        return $0.modDate > $1.modDate
    }
}

// ── Sparkline ───────────────────────────────────────────────────

func sparkline(_ values: [Double]) -> String {
    let chars: [Character] = ["▁","▂","▃","▄","▅","▆","▇","█"]
    let mx = values.max() ?? 1.0
    guard mx > 0 else { return String(repeating: "▁", count: values.count) }
    return String(values.map { chars[min(Int($0 / mx * 7), 7)] })
}

func formatTokens(_ n: Int) -> String {
    if n >= 1_000_000 { return String(format: "%.1fM tok", Double(n) / 1_000_000) }
    if n >= 1_000 { return String(format: "%.0fK tok", Double(n) / 1_000) }
    return "\(n) tok"
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
    var isRebuilding = false  // Prevent overlapping rebuilds
    
    // Shared State (updated in background, rendered in main thread)
    var lastData: MenuData?
    var lastSessions: [SessionInfo] = []
    var lastGhosttyWindows: [GhosttyWindow] = []
    var lastUsage: ClaudeUsage?
    var lastRateLimit: ClaudeRateLimit?
    var rateLimitFetchTick = 0  // fetch rate limit less often (API call)
    var ghosttyFetchTick = 0   // getGhosttyWindows is slow (AppleScript), run every 30s
    var isFetching = false

    // ═════════════════════════════════════════════════════════════════
    // Best Use Cases: Favorites, Recent, Projects
    // ═════════════════════════════════════════════════════════════════

    // Pinned/Favorite Sessions (persisted to UserDefaults)
    var pinnedSessionIds: Set<String> {
        get {
            let defaults = UserDefaults.standard
            let array = defaults.stringArray(forKey: "agimon.pinnedSessions") ?? []
            return Set(array)
        }
        set {
            UserDefaults.standard.set(Array(newValue), forKey: "agimon.pinnedSessions")
        }
    }

    // Recently accessed sessions (max 10, LRU cache)
    var recentSessionIds: [String] {
        get {
            return UserDefaults.standard.stringArray(forKey: "agimon.recentSessions") ?? []
        }
        set {
            // Keep only last 10
            let trimmed = Array(newValue.prefix(10))
            UserDefaults.standard.set(trimmed, forKey: "agimon.recentSessions")
        }
    }

    // Track last access time for smart sorting
    var sessionLastAccessed: [String: Date] {
        get {
            guard let data = UserDefaults.standard.data(forKey: "agimon.sessionAccessTimes"),
                  let dict = try? JSONDecoder().decode([String: Date].self, from: data) else {
                return [:]
            }
            return dict
        }
        set {
            if let data = try? JSONEncoder().encode(newValue) {
                UserDefaults.standard.set(data, forKey: "agimon.sessionAccessTimes")
            }
        }
    }

    // Projects with active sessions for quick navigation
    var activeProjects: [(name: String, path: String, sessions: [SessionInfo])] = []

    // Completion detection: track which sessions were running last tick
    var prevRunningIds: Set<String> = []

    // User-defined session labels (persisted)
    var sessionLabels: [String: String] {
        get { UserDefaults.standard.dictionary(forKey: "agimon.sessionLabels") as? [String: String] ?? [:] }
        set { UserDefaults.standard.set(newValue, forKey: "agimon.sessionLabels") }
    }

    // Burn rate tracking (token cost per hour estimate)
    var burnRateSamples: [(date: Date, cost: Double)] = []

    // Cached all-apps window map — refreshed explicitly, not on every menu rebuild
    var lastAllWindowsByApp: [(String, [ManagedWindow])] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "⚡ …"

        // Fetch data immediately in background on startup
        triggerBackgroundFetch()

        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.onTick()
        }
        RunLoop.current.add(timer!, forMode: .common)
    }

    func triggerBackgroundFetch() {
        guard !isFetching else { return }
        isFetching = true
        DispatchQueue.global(qos: .background).async { [weak self] in
            guard let self = self else { return }
            let newData = fetchMenuData()
            let newSessions = fetchSessionsNative()
            let newUsage = fetchClaudeUsage()
            // Ghostty window fetch (AppleScript) is expensive — run every 30s
            self.ghosttyFetchTick += 1
            let shouldFetchGhostty = self.ghosttyFetchTick % 2 == 1 || self.lastGhosttyWindows.isEmpty
            let newWindows = shouldFetchGhostty ? getGhosttyWindows() : self.lastGhosttyWindows
            DispatchQueue.main.async {
                self.isFetching = false
                if let d = newData { self.lastData = d }

                // ── Completion Detection ─────────────────────────
                let nowRunningIds = Set(newSessions.filter { $0.isRunning }.map { $0.id })
                let justFinished  = self.prevRunningIds.subtracting(nowRunningIds)
                for sid in justFinished {
                    if let s = self.lastSessions.first(where: { $0.id == sid }) {
                        let label = self.sessionLabels[sid] ?? s.message
                        let proj  = s.projectName.isEmpty ? "" : " [\(s.projectName)]"
                        sendNotification(
                            title: "✅ Claude fertig\(proj)",
                            body: String(label.prefix(80))
                        )
                    }
                }
                self.prevRunningIds = nowRunningIds

                // ── Burn Rate Sampling ───────────────────────────
                let cost = newUsage.todayCost
                if cost > 0 {
                    self.burnRateSamples.append((date: Date(), cost: cost))
                    if self.burnRateSamples.count > 12 {
                        self.burnRateSamples.removeFirst(self.burnRateSamples.count - 12)
                    }
                }

                self.lastSessions = newSessions
                if shouldFetchGhostty { self.lastGhosttyWindows = newWindows }
                self.lastUsage = newUsage
                // Rate limit: fetch from API in background every ~60s
                self.rateLimitFetchTick += 1
                let shouldFetchRL = self.rateLimitFetchTick % 4 == 1 || self.lastRateLimit == nil
                if shouldFetchRL {
                    DispatchQueue.global(qos: .utility).async {
                        let rl = fetchClaudeRateLimit()
                        DispatchQueue.main.async {
                            // Notify on rate limit threshold crossings
                            if let rl = rl {
                                let prev = self.lastRateLimit
                                let was5hOk  = (prev?.fiveHour  ?? 0) < 80
                                let was7dOk  = (prev?.sevenDay  ?? 0) < 80
                                if was5hOk  && rl.fiveHour  >= 80 {
                                    sendNotification(title: "⚠️ Claude Max 5h-Limit",
                                        body: String(format: "%.0f%% der 5h-Requests verbraucht (+%dmin)",
                                            rl.fiveHour, rl.fiveHourResetsIn))
                                }
                                if was7dOk && rl.sevenDay >= 80 {
                                    sendNotification(title: "⚠️ Claude Max 7d-Limit",
                                        body: String(format: "%.0f%% der 7d-Requests verbraucht (+%dh)",
                                            rl.sevenDay, rl.sevenDayResetsIn))
                                }
                            }
                            self.lastRateLimit = rl
                            self.updateTitle()
                        }
                    }
                }
                // Also refresh search panel if open
                if let panel = self.searchPanel, panel.isVisible {
                    self.searchData = newSessions
                    self.filteredSessions = newSessions
                    self.searchResults?.reloadData()
                }
                // Incremental content index refresh in background (non-blocking)
                DispatchQueue.global(qos: .utility).async { [weak self] in
                    self?.buildContentIndex()
                    // Refresh content search panel if open (with new data)
                    DispatchQueue.main.async {
                        if let cp = self?.contentPanel, cp.isVisible,
                           let query = self?.contentField?.stringValue, !query.isEmpty {
                            let work = DispatchWorkItem { [weak self] in
                                let hits = self?.searchContent(query) ?? []
                                DispatchQueue.main.async {
                                    self?.contentHits = hits
                                    self?.contentTable?.reloadData()
                                }
                            }
                            DispatchQueue.global(qos: .userInitiated).async(execute: work)
                        }
                    }
                }
                self.updateTitle()
                self.rebuildMenu()
            }
        }
    }

    func onTick() {
        tickCount += 1
        // Every 5s: update title from cached data (instant)
        updateTitle()
        // Every 15s: background-fetch fresh data, then rebuild menu
        if tickCount % 3 == 0 { triggerBackgroundFetch() }
    }

    // Compute live $/hr from sampled cost data
    func currentBurnRate() -> Double? {
        guard burnRateSamples.count >= 2 else { return nil }
        let first = burnRateSamples.first!
        let last  = burnRateSamples.last!
        let hrs   = last.date.timeIntervalSince(first.date) / 3600.0
        guard hrs > 0.01 else { return nil }
        let delta = last.cost - first.cost
        guard delta > 0 else { return nil }
        return delta / hrs
    }

    func updateTitle() {
        guard let data = lastData else {
            statusItem.button?.title = "◇ …"
            return
        }

        let claudeProcs = data.procs.filter { $0.cat == "claude" }
        let claudeActive = claudeProcs.filter { $0.s == "active" }.count
        let claudeTotal = claudeProcs.count
        let icon = claudeTotal > 0 ? "⚡" : "◇"

        // Burn rate: show if active sessions
        var burnStr = ""
        if claudeActive > 0, let rate = currentBurnRate(), rate > 0.01 {
            burnStr = "  🔥$\(String(format: "%.2f", rate))/h"
        }

        let costSuffix = lastUsage.map { u in
            u.todayCost > 0 ? "  $\(String(format: "%.0f", u.todayCost))" : ""
        } ?? ""
        if let rl = lastRateLimit {
            let fhPct = Int(rl.fiveHour.rounded())
            let sdPct = Int(rl.sevenDay.rounded())
            let warn = (rl.fiveHour > 80 || rl.sevenDay > 80) ? "⚠️" : ""
            let warnStr = warn.isEmpty ? "" : " \(warn)"
            statusItem.button?.title = "\(icon) \(claudeActive)/\(claudeTotal)  \(fhPct)%·\(sdPct)%\(warnStr)\(costSuffix)\(burnStr)"
        } else {
            statusItem.button?.title = "\(icon) \(claudeActive)/\(claudeTotal)\(costSuffix)\(burnStr)"
        }
    }

    // Shared session submenu used by Zone 1.1 (running) and Zone 5 (history)
    func sessionSubmenuInline(_ s: SessionInfo) -> NSMenu {
        let sub = NSMenu()
        let customLabel = sessionLabels[s.id]
        let renameTitle = customLabel == nil ? "🏷️ Label vergeben…" : "🏷️ Label: \"\(customLabel!)\""
        let renameItem = NSMenuItem(title: renameTitle, action: #selector(renameSession(_:)), keyEquivalent: "")
        renameItem.target = self; renameItem.representedObject = s.id; sub.addItem(renameItem)
        sub.addItem(.separator())
        let resumeTab = NSMenuItem(title: "▶ Resume in neuem Tab", action: #selector(resumeSessionTab(_:)), keyEquivalent: "")
        resumeTab.target = self; resumeTab.representedObject = s.id; sub.addItem(resumeTab)
        let resumeWin = NSMenuItem(title: "⎗  Resume in neuem Fenster", action: #selector(resumeSession(_:)), keyEquivalent: "")
        resumeWin.target = self; resumeWin.representedObject = s.id; sub.addItem(resumeWin)
        sub.addItem(.separator())
        if !s.cwd.isEmpty {
            let cwdItem = NSMenuItem(title: "📂 \(s.projectName.isEmpty ? URL(fileURLWithPath: s.cwd).lastPathComponent : s.projectName)", action: #selector(openInFinder(_:)), keyEquivalent: "")
            cwdItem.target = self; cwdItem.representedObject = s.cwd; sub.addItem(cwdItem)
            let ideItem = NSMenuItem(title: "📝 In Windsurf öffnen", action: #selector(openInIde(_:)), keyEquivalent: "")
            ideItem.target = self; ideItem.representedObject = s.cwd; sub.addItem(ideItem)
            sub.addItem(.separator())
        }
        sub.addItem(styledItem("  📊 \(s.msgCount) Msg · \(s.timeAgo) ago", color: Palette.muted))
        if s.isRunning {
            let killItem = NSMenuItem(title: "❌ Stop (PID \(s.runningPid))", action: #selector(killProcess(_:)), keyEquivalent: "")
            killItem.target = self; killItem.representedObject = Int(s.runningPid) ?? 0; sub.addItem(killItem)
        }
        sub.addItem(.separator())
        let copyCmd = NSMenuItem(title: "📋 Resume-CMD kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
        copyCmd.target = self; copyCmd.representedObject = "claude -r \(s.id) --dangerously-skip-permissions"
        sub.addItem(copyCmd)
        let pinned = pinnedSessionIds
        if pinned.contains(s.id) {
            let unpin = NSMenuItem(title: "📌 Loslösen", action: #selector(unpinSession(_:)), keyEquivalent: "")
            unpin.target = self; unpin.representedObject = s.id; sub.addItem(unpin)
        } else {
            let pin = NSMenuItem(title: "📌 Anpinnen", action: #selector(pinSession(_:)), keyEquivalent: "")
            pin.target = self; pin.representedObject = s.id; sub.addItem(pin)
        }
        return sub
    }

    func rebuildMenu() {
        guard !isRebuilding else { return }
        isRebuilding = true
        defer { isRebuilding = false }

        let menu = NSMenu()
        menu.autoenablesItems = false

        // ════════════════════════════════════════════════════
        // ZONE 1 — QUICK ACTIONS (schlank: max 5 Items)
        // ════════════════════════════════════════════════════

        let searchItem = NSMenuItem(title: "🔍 Sessions suchen…", action: #selector(openSessionSearch), keyEquivalent: "f")
        searchItem.target = self; searchItem.keyEquivalentModifierMask = [.command]
        menu.addItem(searchItem)

        let contentItem = NSMenuItem(title: "📖 Inhalte durchsuchen…", action: #selector(openContentSearch), keyEquivalent: "f")
        contentItem.target = self; contentItem.keyEquivalentModifierMask = [.command, .shift]
        menu.addItem(contentItem)

        let newSessItem = NSMenuItem(title: "＋ Neue Claude-Session", action: #selector(launchClaude(_:)), keyEquivalent: "n")
        newSessItem.target = self; newSessItem.representedObject = NSHomeDirectory()
        newSessItem.keyEquivalentModifierMask = [.command]
        menu.addItem(newSessItem)

        let focusModeItem = NSMenuItem(title: "🎯 Focus Mode  (Idle killen + Grid)", action: #selector(activateFocusMode), keyEquivalent: "0")
        focusModeItem.target = self; focusModeItem.keyEquivalentModifierMask = [.command, .option]
        menu.addItem(focusModeItem)

        let refreshItem = NSMenuItem(title: "↺ Aktualisieren", action: #selector(manualRefresh), keyEquivalent: "r")
        refreshItem.target = self; refreshItem.keyEquivalentModifierMask = [.command]
        menu.addItem(refreshItem)

        menu.addItem(.separator())

        // ════════════════════════════════════════════════════
        // ZONE 1.1 — LÄUFT JETZT (sichtbarste Info zuerst)
        // ════════════════════════════════════════════════════

        let runningNow = lastSessions.filter { $0.isRunning }
        if !runningNow.isEmpty {
            // Inline burn rate next to running count
            let burnSuffix = currentBurnRate().map { "  🔥$\(String(format: "%.2f", $0))/h" } ?? ""
            let runSec = styledItem("🟢 Läuft jetzt (\(runningNow.count))\(burnSuffix)", color: Palette.alive, bold: true)
            let runSub = NSMenu()
            for s in runningNow {
                let proj = s.projectName.isEmpty ? "" : " [\(s.projectName)]"
                let ag = s.agents > 0 ? " ·\(s.agents)ag" : ""
                let displayMsg = sessionLabels[s.id] ?? s.message
                let item = styledItem("● \(displayMsg)\(proj)\(ag)", color: Palette.alive)
                item.submenu = sessionSubmenuInline(s)
                runSub.addItem(item)
            }
            // Kill All only shown inside this running block
            runSub.addItem(.separator())
            let killAllItem = NSMenuItem(title: "⛔ Alle stoppen", action: #selector(killAllClaude), keyEquivalent: "")
            killAllItem.target = self; runSub.addItem(killAllItem)
            runSec.submenu = runSub
            menu.addItem(runSec)
            menu.addItem(.separator())
        }

        // ════════════════════════════════════════════════════
        // ZONE 1.5 — BEST USE CASES: Favorites, Recent, Projects
        // ════════════════════════════════════════════════════

        // ⭐ PINNED FAVORITES (persisted)
        let pinned = pinnedSessionIds
        if !pinned.isEmpty {
            let pinnedHeader = styledItem("⭐ Gepinnte Favoriten  (\(pinned.count))", color: Palette.gold, bold: true)
            let pinnedSub = NSMenu()

            for sessionId in pinned {
                if let session = lastSessions.first(where: { $0.id == sessionId }) {
                    let status = session.isRunning ? "🟢" : "⏸️"
                    let title = session.message.isEmpty ? "Session \(sessionId.prefix(8))" : String(session.message.prefix(30))
                    let item = NSMenuItem(title: "\(status) \(title)", action: #selector(resumeSessionTab(_:)), keyEquivalent: "")
                    item.target = self; item.representedObject = sessionId

                    // Submenu for pinned items
                    let sub = NSMenu()
                    let unpin = NSMenuItem(title: "📌 Loslösen", action: #selector(unpinSession(_:)), keyEquivalent: "")
                    unpin.target = self; unpin.representedObject = sessionId; sub.addItem(unpin)

                    if session.isRunning {
                        let focus = NSMenuItem(title: "🪟 Fenster fokussieren", action: #selector(focusSessionWindow(_:)), keyEquivalent: "")
                        focus.target = self; focus.representedObject = sessionId; sub.addItem(focus)
                    } else {
                        let resume = NSMenuItem(title: "▶ Fortsetzen", action: #selector(resumeSessionTab(_:)), keyEquivalent: "")
                        resume.target = self; resume.representedObject = sessionId; sub.addItem(resume)
                    }

                    if !session.projectName.isEmpty {
                        sub.addItem(styledItem("📁 \(session.projectName)", color: Palette.muted))
                    }

                    item.submenu = sub
                    pinnedSub.addItem(item)
                }
            }

            // Batch actions for pinned
            pinnedSub.addItem(.separator())
            let resumeAll = NSMenuItem(title: "▶ Alle gepinnten fortsetzen", action: #selector(resumeAllPinned), keyEquivalent: "")
            resumeAll.target = self; pinnedSub.addItem(resumeAll)

            let clearPins = NSMenuItem(title: "📌 Alle loslösen", action: #selector(clearAllPins), keyEquivalent: "")
            clearPins.target = self; pinnedSub.addItem(clearPins)

            pinnedHeader.submenu = pinnedSub
            menu.addItem(pinnedHeader)
        }

        // 🕐 RECENT SESSIONS (last 5 accessed)
        let recent = recentSessionIds.prefix(5)
        if !recent.isEmpty {
            let recentHeader = styledItem("🕐 Zuletzt verwendet", color: Palette.amber, bold: true)
            let recentSub = NSMenu()

            for (idx, sessionId) in recent.enumerated() {
                if let session = lastSessions.first(where: { $0.id == sessionId }) {
                    let status = session.isRunning ? "🟢" : "⚪"
                    let title = session.message.isEmpty ? "Session \(sessionId.prefix(8))" : String(session.message.prefix(30))
                    let key = idx < 3 ? String(idx + 1) : ""  // ⌘1, ⌘2, ⌘3 for first 3
                    let item = NSMenuItem(title: "\(status) \(title)", action: #selector(resumeSessionTab(_:)), keyEquivalent: key)
                    item.target = self; item.representedObject = sessionId
                    if !key.isEmpty { item.keyEquivalentModifierMask = [.command] }

                    // Add pin option to recent items
                    if !pinned.contains(sessionId) {
                        let sub = NSMenu()
                        let pin = NSMenuItem(title: "📌 Anpinnen", action: #selector(pinSession(_:)), keyEquivalent: "")
                        pin.target = self; pin.representedObject = sessionId; sub.addItem(pin)
                        item.submenu = sub
                    }

                    recentSub.addItem(item)
                }
            }

            recentSub.addItem(.separator())
            let clearRecent = NSMenuItem(title: "🕐 Verlauf löschen", action: #selector(clearAllRecents), keyEquivalent: "")
            clearRecent.target = self; recentSub.addItem(clearRecent)

            recentHeader.submenu = recentSub
            menu.addItem(recentHeader)
        }

        // 📁 PROJECTS (group by project)
        let projectGroups = buildProjectGroups().prefix(5)
        if projectGroups.count > 1 {
            let projHeader = styledItem("📁 Projekte  (\(projectGroups.count))", color: Palette.sage, bold: true)
            let projSub = NSMenu()

            for (name, path, sessions) in projectGroups where !name.isEmpty {
                let item = styledItem("📁 \(name)  (\(sessions.count) Sessions)", color: Palette.muted)
                let sub = NSMenu()

                // Quick actions for project
                let openFinder = NSMenuItem(title: "📂 Im Finder öffnen", action: #selector(openInFinder(_:)), keyEquivalent: "")
                openFinder.target = self; openFinder.representedObject = path; sub.addItem(openFinder)

                let newHere = NSMenuItem(title: "＋ Neue Session hier", action: #selector(launchClaude(_:)), keyEquivalent: "")
                newHere.target = self; newHere.representedObject = path; sub.addItem(newHere)

                sub.addItem(.separator())

                // Sessions in this project
                for session in sessions.prefix(5) {
                    let status = session.isRunning ? "🟢" : "⚪"
                    let title = session.message.isEmpty ? "Session \(session.id.prefix(8))" : String(session.message.prefix(25))
                    let sItem = NSMenuItem(title: "\(status) \(title)", action: #selector(resumeSessionTab(_:)), keyEquivalent: "")
                    sItem.target = self; sItem.representedObject = session.id
                    sub.addItem(sItem)
                }

                if sessions.count > 5 {
                    sub.addItem(styledItem("  … und \(sessions.count - 5) weitere", color: Palette.muted))
                }

                item.submenu = sub
                projSub.addItem(item)
            }

            projHeader.submenu = projSub
            menu.addItem(projHeader)
        }

        if !pinned.isEmpty || !recent.isEmpty || projectGroups.count > 1 {
            menu.addItem(.separator())
        }

        // ════════════════════════════════════════════════════
        // ZONE 2 — GHOSTTY FENSTER (⌘⌥1-9 hotkeys)
        // ════════════════════════════════════════════════════

        let digits = ["1","2","3","4","5","6","7","8","9"]
        let gwCount = lastGhosttyWindows.count
        let winHeader = styledItem(
            gwCount > 0 ? "🪟 Ghostty  (\(gwCount) Fenster)" : "🪟 Ghostty  (keine Fenster)",
            color: Palette.violet, bold: true
        )
        let winSub = NSMenu()

        for (idx, gw) in lastGhosttyWindows.enumerated() {
            let sess = lastSessions.first { $0.id == gw.sessionId }

            // Human-readable label without technical IDs
            let statusIcon = gw.sessionId.isEmpty ? (gw.pid.isEmpty ? "⬜" : "🟡") : "🟢"
            let title: String
            if let s = sess, !s.message.isEmpty {
                // Use session message (first user message) as title
                title = String(s.message.prefix(35))
            } else if let s = sess, !s.projectName.isEmpty {
                // Use project name if no message
                title = "📁 \(s.projectName)"
            } else if !gw.sessionId.isEmpty {
                // Short session reference without full ID
                title = "Session \(gw.sessionId.prefix(6))…"
            } else if !gw.pid.isEmpty {
                title = "Neue Claude Session"
            } else {
                title = "Terminal \(gw.tty)"
            }

            let detail = sess.flatMap { $0.projectName.isEmpty ? nil : "· \($0.projectName)" } ?? ""
            let label = "\(statusIcon) \(title) \(detail)".trimmingCharacters(in: .whitespaces)

            let hotkey = idx < digits.count ? digits[idx] : ""
            let wItem = NSMenuItem(title: label, action: #selector(focusWindow(_:)), keyEquivalent: hotkey)
            wItem.target = self; wItem.representedObject = gw.windowIndex
            if !hotkey.isEmpty { wItem.keyEquivalentModifierMask = [.command, .option] }

            let wSub = NSMenu()
            let focusIt = NSMenuItem(title: "🪟 Fenster fokussieren", action: #selector(focusWindow(_:)), keyEquivalent: "")
            focusIt.target = self; focusIt.representedObject = gw.windowIndex; wSub.addItem(focusIt)

            if !gw.sessionId.isEmpty {
                let rt = NSMenuItem(title: "▶ In neuem Tab fortsetzen", action: #selector(resumeSessionTab(_:)), keyEquivalent: "")
                rt.target = self; rt.representedObject = gw.sessionId; wSub.addItem(rt)

                let cc = NSMenuItem(title: "📋 Befehl kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
                cc.target = self
                cc.representedObject = "claude -r \(gw.sessionId) --dangerously-skip-permissions"
                wSub.addItem(cc)

                if let s = sess {
                    wSub.addItem(.separator())
                    let info = "📊 \(s.msgCount) Nachrichten · vor \(s.timeAgo)"
                    wSub.addItem(styledItem(info, color: Palette.muted))

                    if !s.cwd.isEmpty {
                        let cwd = NSMenuItem(title: "📁 Ordner: \(URL(fileURLWithPath: s.cwd).lastPathComponent)", action: #selector(openInFinder(_:)), keyEquivalent: "")
                        cwd.target = self; cwd.representedObject = s.cwd; wSub.addItem(cwd)
                    }
                }
            }
            wItem.submenu = wSub
            winSub.addItem(wItem)
        }

        if lastGhosttyWindows.isEmpty {
            winSub.addItem(styledItem("  (keine Ghostty-Fenster erkannt)", color: Palette.muted))
        } else {
            // Grid alignment submenu
            winSub.addItem(.separator())
            let alignItem = styledItem("📐 Fenster anordnen…", color: Palette.gold)
            let alignSub = NSMenu()

            let grid2 = NSMenuItem(title: "2×2 Grid (4 Fenster)", action: #selector(arrange2x2), keyEquivalent: "")
            grid2.target = self
            alignSub.addItem(grid2)

            let grid3 = NSMenuItem(title: "3×3 Grid (9 Fenster)", action: #selector(arrange3x3), keyEquivalent: "")
            grid3.target = self
            alignSub.addItem(grid3)

            let grid4 = NSMenuItem(title: "4×4 Grid (16 Fenster)", action: #selector(arrange4x4), keyEquivalent: "")
            grid4.target = self
            alignSub.addItem(grid4)

            alignSub.addItem(.separator())

            let autoItem = NSMenuItem(title: "🎨 Auto-Arrange (passend zu Sessions)", action: #selector(autoArrangeGhostty), keyEquivalent: "")
            autoItem.target = self
            alignSub.addItem(autoItem)

            let titleItem = NSMenuItem(title: "🏷️ Titel aktualisieren (Projekt-Namen)", action: #selector(updateGhosttyTitles), keyEquivalent: "")
            titleItem.target = self
            alignSub.addItem(titleItem)

            alignItem.submenu = alignSub
            winSub.addItem(alignItem)
        }
        winHeader.submenu = winSub
        menu.addItem(winHeader)
        menu.addItem(.separator())

        // ════════════════════════════════════════════════════
        // ZONE 2.5 — ALLE APPS (Lazy: on-demand, nicht bei jedem rebuild)
        // ════════════════════════════════════════════════════
        // Cached: appsWithWindows wird nur geladen wenn User auf diesen Item klickt
        // (NSMenu.delegate würde dies vollständig lösen; hier nutzen wir cached lastAllWindows)

        let cachedApps = lastAllWindowsByApp  // cached from previous getWindowsByApp() call
        let allAppsCount = cachedApps.reduce(0) { $0 + $1.1.count }
        let windowHeader = styledItem(
            "🪟 Alle Apps  (\(cachedApps.count) · \(allAppsCount) Fenster)",
            color: Palette.teal, bold: true
        )
        let windowSub = NSMenu()

        let gatherAll = NSMenuItem(title: "📥 Alle Fenster sammeln", action: #selector(gatherAllWindows), keyEquivalent: "")
        gatherAll.target = self; windowSub.addItem(gatherAll)

        let arrangeAll = NSMenuItem(title: "🎨 Auto-Arrange alle Apps", action: #selector(arrangeAllApps), keyEquivalent: "")
        arrangeAll.target = self; windowSub.addItem(arrangeAll)

        let refreshWin = NSMenuItem(title: "↺ Fensterliste aktualisieren", action: #selector(refreshAllWindows), keyEquivalent: "")
        refreshWin.target = self; windowSub.addItem(refreshWin)

        if !cachedApps.isEmpty {
            windowSub.addItem(.separator())
            for (appName, windows) in cachedApps.prefix(8) {
                let appItem = styledItem("\(windows.first?.appIcon ?? "🪟") \(appName)  (\(windows.count))", color: Palette.muted)
                let appSub = NSMenu()
                let grid2 = NSMenuItem(title: "📐 2×2 Grid", action: #selector(arrangeSpecificApp(_:)), keyEquivalent: "")
                grid2.target = self; grid2.representedObject = ["app": appName, "layout": "2x2"]; appSub.addItem(grid2)
                let cascade = NSMenuItem(title: "📂 Gestaffelt", action: #selector(cascadeSpecificApp(_:)), keyEquivalent: "")
                cascade.target = self; cascade.representedObject = appName; appSub.addItem(cascade)
                let gather = NSMenuItem(title: "📥 Hier sammeln", action: #selector(gatherSpecificApp(_:)), keyEquivalent: "")
                gather.target = self; gather.representedObject = appName; appSub.addItem(gather)
                appSub.addItem(.separator())
                for win in windows.prefix(4) {
                    let wItem = NSMenuItem(title: win.humanLabel, action: #selector(focusManagedWindow(_:)), keyEquivalent: "")
                    wItem.target = self; wItem.representedObject = ["pid": NSNumber(value: win.pid), "title": win.title]
                    appSub.addItem(wItem)
                }
                if windows.count > 4 { appSub.addItem(styledItem("  +\(windows.count-4) weitere", color: Palette.muted)) }
                appItem.submenu = appSub
                windowSub.addItem(appItem)
            }
        }

        windowHeader.submenu = windowSub
        menu.addItem(windowHeader)
        menu.addItem(.separator())

        // ════════════════════════════════════════════════════
        // ZONE 3 — CLAUDE STATUS (Rate Limits + Kosten, merged)
        // ════════════════════════════════════════════════════

        func ratioBar(_ pct: Double, width: Int = 12) -> String {
            let filled = min(Int(pct / 100 * Double(width)), width)
            let fill = pct > 80 ? String(repeating: "█", count: filled)
                                : String(repeating: "▓", count: filled)
            return fill + String(repeating: "░", count: width - filled)
        }

        // Build single "Claude Status" header combining rate limits + today cost
        let claudeStatusSec: NSMenuItem
        let claudeStatusSub = NSMenu()

        let todayCostStr = lastUsage.map { u in
            u.todayCost > 0 ? "  💰$\(String(format: "%.2f", u.todayCost))" : ""
        } ?? ""

        if let rl = lastRateLimit {
            let fhPct = rl.fiveHour; let sdPct = rl.sevenDay
            let worst = max(fhPct, sdPct)
            let dot = worst > 90 ? "🔴" : (worst > 60 ? "🟡" : "🟢")
            let fhColor: NSColor = fhPct > 90 ? Palette.danger : (fhPct > 60 ? Palette.warn : Palette.alive)
            let sdColor: NSColor = sdPct > 90 ? Palette.danger : (sdPct > 60 ? Palette.warn : Palette.alive)

            claudeStatusSec = styledItem(
                "\(dot) Claude \(rl.plan)  5h \(Int(fhPct.rounded()))%  7d \(Int(sdPct.rounded()))%\(todayCostStr)",
                color: worst > 60 ? Palette.warn : Palette.alive, bold: true
            )

            // Rate limit bars
            let fhReset = rl.fiveHourResetsIn > 0 ? "  reset \(rl.fiveHourResetsIn)min" : ""
            claudeStatusSub.addItem(styledItem("5h   \(ratioBar(fhPct)) \(String(format: "%3.0f%%", fhPct))\(fhReset)", color: fhColor, mono: true))
            let sdReset = rl.sevenDayResetsIn > 0 ? "  reset \(rl.sevenDayResetsIn)h" : ""
            claudeStatusSub.addItem(styledItem("7d   \(ratioBar(sdPct)) \(String(format: "%3.0f%%", sdPct))\(sdReset)", color: sdColor, mono: true))
            if let son = rl.sevenDaySonnet {
                let sonColor: NSColor = son > 90 ? Palette.danger : (son > 60 ? Palette.warn : Palette.teal)
                claudeStatusSub.addItem(styledItem("son  \(ratioBar(son)) \(String(format: "%3.0f%%", son))  (Sonnet 7d)", color: sonColor, mono: true))
            }
            if let eu = rl.extraUsed, let el = rl.extraLimit, let ep = rl.extraPct, el > 0 {
                let exColor: NSColor = ep >= 100 ? Palette.danger : (ep > 80 ? Palette.warn : Palette.muted)
                claudeStatusSub.addItem(.separator())
                claudeStatusSub.addItem(styledItem("extra \(ratioBar(ep)) $\(Int(max(0,eu)))/$\(Int(el)) · \(String(format: "%.0f", ep))%", color: exColor, mono: true))
                if ep >= 100 { claudeStatusSub.addItem(styledItem("  ⚠️ Extra-Budget ausgeschöpft!", color: Palette.danger, bold: true)) }
            }
            let ageStr = rl.cacheAge == 0 ? "live" : "\(rl.cacheAge)s alt"
            claudeStatusSub.addItem(styledItem("  \(ageStr) · Anthropic API", color: Palette.subtle))
        } else {
            claudeStatusSec = styledItem("🔄 Claude Status  (lädt…)", color: Palette.muted, bold: true)
            claudeStatusSub.addItem(styledItem("  (wird abgerufen…)", color: Palette.muted))
        }

        // Cost details inline
        if let u = lastUsage {
            claudeStatusSub.addItem(.separator())
            let modelStr = u.modelsToday.isEmpty ? "" : "  [\(u.modelsToday.joined(separator:"·"))]"
            claudeStatusSub.addItem(styledItem("Heute      $\(String(format: "%.2f", u.todayCost))  \(formatTokens(u.todayTokens))\(modelStr)", color: Palette.text))
            claudeStatusSub.addItem(styledItem("Gestern    $\(String(format: "%.2f", u.yesterdayCost))", color: Palette.muted))
            claudeStatusSub.addItem(styledItem("7 Tage     $\(String(format: "%.2f", u.weekCost))  \(formatTokens(u.weekTokens))", color: Palette.muted))
            claudeStatusSub.addItem(styledItem("All-Time   $\(String(format: "%.0f", u.allTimeCost))  (\(u.days) Tage)", color: Palette.subtle))
            if let b = lastData?.budget, b.budget > 0 {
                let pct = b.spent / b.budget * 100
                let bar = String(repeating: "█", count: min(Int(pct/100*12),12)) + String(repeating: "░", count: max(0,12-min(Int(pct/100*12),12)))
                let bc = pct > 100 ? Palette.danger : (pct > 80 ? Palette.warn : Palette.alive)
                claudeStatusSub.addItem(.separator())
                claudeStatusSub.addItem(styledItem("Budget \(bar) $\(String(format: "%.2f", b.spent))/$\(String(format: "%.0f", b.budget))", color: bc, mono: true))
            }
        }

        claudeStatusSub.addItem(.separator())
        let statsBtn = NSMenuItem(title: "📊 Usage Dashboard öffnen", action: #selector(openClaudeStats), keyEquivalent: "u")
        statsBtn.target = self; statsBtn.keyEquivalentModifierMask = [.command]; claudeStatusSub.addItem(statsBtn)

        claudeStatusSec.submenu = claudeStatusSub
        menu.addItem(claudeStatusSec)

        // Warnings inline (only if issues exist — otherwise hidden)
        guard let data = lastData else {
            menu.addItem(.separator())
            menu.addItem(styledItem("⚡ AGIMON lädt…", color: Palette.muted))
            let qI = NSMenuItem(title: "Beenden", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
            menu.addItem(qI)
            statusItem.menu = menu; return
        }

        let allIssues = data.watchdog + data.budget.alerts
        if !allIssues.isEmpty {
            let warnSec = styledItem("⚠️ Warnungen (\(allIssues.count))", color: Palette.warn, bold: true)
            let warnSub = NSMenu()
            for issue in allIssues {
                let isDown = issue.hasPrefix("DOWN:")
                let isHung = issue.hasPrefix("HUNG?:")
                let icon = isDown ? "🔴" : (isHung ? "🟡" : "⚠️")
                let issueItem = styledItem("\(icon) \(issue)", color: isDown ? Palette.danger : Palette.warn)
                let fixSub = NSMenu()
                if issue.contains("docker") {
                    let s = NSMenuItem(title: "▶ Docker starten", action: #selector(autoFix(_:)), keyEquivalent: "")
                    s.target = self; s.representedObject = "open -a Docker"; fixSub.addItem(s)
                } else if issue.contains("qdrant") {
                    let s = NSMenuItem(title: "▶ Qdrant starten", action: #selector(autoFix(_:)), keyEquivalent: "")
                    s.target = self; s.representedObject = "docker run -d -p 6333:6333 qdrant/qdrant"; fixSub.addItem(s)
                } else if isHung {
                    let parts = issue.components(separatedBy: " ")
                    if let pidIdx = parts.firstIndex(of: "PID"), pidIdx + 1 < parts.count {
                        let pid = parts[pidIdx + 1]
                        let kI = NSMenuItem(title: "❌ Kill \(pid)", action: #selector(autoFix(_:)), keyEquivalent: "")
                        kI.target = self; kI.representedObject = "kill \(pid)"; fixSub.addItem(kI)
                        let fI = NSMenuItem(title: "🪟 Fokussieren", action: #selector(focusWindowForPid(_:)), keyEquivalent: "")
                        fI.target = self; fI.representedObject = pid; fixSub.addItem(fI)
                    }
                }
                let cp = NSMenuItem(title: "📋 Kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
                cp.target = self; cp.representedObject = issue; fixSub.addItem(cp)
                if !fixSub.items.isEmpty { issueItem.submenu = fixSub }
                warnSub.addItem(issueItem)
            }
            warnSec.submenu = warnSub
            menu.addItem(warnSec)
        }

        menu.addItem(.separator())

        // ════════════════════════════════════════════════════
        // ZONE 5 — VERLAUF (Heute + Woche; Running ist in Zone 1.1)
        // ════════════════════════════════════════════════════

        let cal = Calendar.current
        let todaySessions = lastSessions.filter { !$0.isRunning && cal.isDateInToday($0.modDate) }
        let weekSessions  = lastSessions.filter { !$0.isRunning && !cal.isDateInToday($0.modDate) }

        // 📅 Heute
        if !todaySessions.isEmpty {
            let sec = styledItem("📅 Heute (\(todaySessions.count))", color: Palette.gold, bold: true)
            let sub = NSMenu()
            for s in todaySessions.prefix(10) {
                let proj = s.projectName.isEmpty ? "" : " [\(s.projectName)]"
                let timeStr = DateFormatter.localizedString(from: s.modDate, dateStyle: .none, timeStyle: .short)
                let displayMsg = sessionLabels[s.id] ?? s.message
                let item = styledItem("○ \(timeStr)  \(displayMsg)\(proj)", color: Palette.text)
                item.submenu = sessionSubmenuInline(s)
                sub.addItem(item)
            }
            sec.submenu = sub
            menu.addItem(sec)
        }

        // 📜 Diese Woche
        if !weekSessions.isEmpty {
            let sec = styledItem("📜 Diese Woche (\(weekSessions.count))", color: Palette.subtle, bold: true)
            let sub = NSMenu()
            for s in weekSessions.prefix(15) {
                let proj = s.projectName.isEmpty ? "" : " [\(s.projectName)]"
                let displayMsg = sessionLabels[s.id] ?? s.message
                let item = styledItem("○ \(s.timeAgo)  \(displayMsg)\(proj)", color: Palette.muted)
                item.submenu = sessionSubmenuInline(s)
                sub.addItem(item)
            }
            sec.submenu = sub
            menu.addItem(sec)
        }
        menu.addItem(.separator())

        // ════════════════════════════════════════════════════
        // ZONE 6 — PROZESSE (kompakt, nur Top-CPU-Killer)
        // ════════════════════════════════════════════════════

        let cats: [(String, String, NSColor)] = [
            ("claude",   "💻 Claude",   Palette.gold),
            ("dev-tool", "🔧 Dev",      Palette.teal),
            ("ide",      "📝 IDE",      Palette.violet),
            ("infra",    "🐳 Infra",    Palette.sage),
            ("runtime",  "⚙️ Runtime",  Palette.muted),
        ]
        var procItems: [NSMenuItem] = []
        for (cat, label, color) in cats {
            let catProcs = data.procs.filter { $0.cat == cat }
            guard !catProcs.isEmpty else { continue }
            let sorted = catProcs.sorted { $0.cpu > $1.cpu }
            let catCpu = catProcs.reduce(0.0) { $0 + $1.cpu }
            let catMem = catProcs.reduce(0) { $0 + $1.mem }
            let sub = NSMenu()
            for p in sorted.prefix(8) {
                let dot = p.s == "active" ? "●" : "○"
                let cpuStr = String(format: "%4.0f%%", p.cpu)
                let pItem = styledItem(
                    "\(dot) \(p.label)  \(cpuStr)  \(p.mem)MB",
                    color: p.cpu > 50 ? Palette.warn : (p.s == "active" ? Palette.alive : Palette.muted),
                    mono: true
                )
                let pSub = NSMenu()
                pSub.addItem(styledItem("PID \(p.pid)  \(p.label)", color: Palette.muted))
                let ki = NSMenuItem(title: "❌ Kill \(p.pid)", action: #selector(killProcess(_:)), keyEquivalent: "")
                ki.target = self; ki.representedObject = p.pid; pSub.addItem(ki)
                let di = NSMenuItem(title: "🔍 Details", action: #selector(showProcessDetail(_:)), keyEquivalent: "")
                di.target = self; di.representedObject = p.pid; pSub.addItem(di)
                pItem.submenu = pSub
                sub.addItem(pItem)
            }
            let catItem = styledItem(
                "\(label) (\(catProcs.count))  \(String(format: "%.0f", catCpu))%  \(catMem/1024)GB",
                color: color, bold: true
            )
            catItem.submenu = sub
            procItems.append(catItem)
        }
        if !procItems.isEmpty {
            let totalCpu = data.procs.reduce(0.0) { $0 + $1.cpu }
            let totalMem = data.procs.reduce(0) { $0 + $1.mem }
            let sysSec = styledItem(
                "⚙️ System  \(String(format: "%.0f", totalCpu))% CPU  \(totalMem/1024)GB RAM",
                color: Palette.muted, bold: true
            )
            let sysSub = NSMenu()
            for pi in procItems { sysSub.addItem(pi) }
            sysSec.submenu = sysSub
            menu.addItem(sysSec)
            menu.addItem(.separator())
        }

        // ── AI Tools ──────────────────────────────────────────────
        let aiSec = styledItem("🤖 AI Tools", color: Palette.gold, bold: true)
        let aiSub = NSMenu()

        // Use mlx data from lastData (already fetched, no extra call)
        let mlx = data.mlx
        let ollamaOK = mlx.available
        let qdrantOK = !data.watchdog.contains(where: { $0.contains("qdrant") && $0.hasPrefix("DOWN") })
        let olDot = ollamaOK ? "🟢" : "🔴"
        let qdDot = qdrantOK ? "🟢" : "🔴"

        // Ollama status + models
        aiSub.addItem(styledItem("\(olDot) Ollama  \(mlx.count > 0 ? "— \(mlx.count) Modelle" : "offline")", color: ollamaOK ? Palette.alive : Palette.danger, bold: true))
        if ollamaOK && !mlx.models.isEmpty {
            let modelSec = NSMenu()
            for model in mlx.models {
                let mItem = NSMenuItem(title: "▶ \(model)", action: #selector(runOllamaModel(_:)), keyEquivalent: "")
                mItem.target = self; mItem.representedObject = model
                let mSub = NSMenu()
                let run = NSMenuItem(title: "▶ In Terminal starten", action: #selector(runOllamaModel(_:)), keyEquivalent: "")
                run.target = self; run.representedObject = model; mSub.addItem(run)
                let copy = NSMenuItem(title: "� Name kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
                copy.target = self; copy.representedObject = model; mSub.addItem(copy)
                let cpCmd = NSMenuItem(title: "� ollama run … kopieren", action: #selector(copyText(_:)), keyEquivalent: "")
                cpCmd.target = self; cpCmd.representedObject = "ollama run \(model)"; mSub.addItem(cpCmd)
                mItem.submenu = mSub
                modelSec.addItem(mItem)
            }
            let modelsEntry = styledItem("  Modelle:", color: Palette.muted)
            modelsEntry.submenu = modelSec
            aiSub.addItem(modelsEntry)
        } else if !ollamaOK {
            let startOl = NSMenuItem(title: "  ▶ Ollama starten", action: #selector(startOllama), keyEquivalent: "")
            startOl.target = self; aiSub.addItem(startOl)
        }
        aiSub.addItem(.separator())

        // Qdrant status
        aiSub.addItem(styledItem("\(qdDot) Qdrant \(qdrantOK ? "online" : "offline")", color: qdrantOK ? Palette.alive : Palette.danger, bold: true))
        if qdrantOK {
            let qdOpen = NSMenuItem(title: "  🌐 Dashboard öffnen", action: #selector(openUrl(_:)), keyEquivalent: "")
            qdOpen.target = self; qdOpen.representedObject = "http://localhost:6333/dashboard"; aiSub.addItem(qdOpen)
            let qdCli = NSMenuItem(title: "  🔍 CLI Suche", action: #selector(openQdrantCli), keyEquivalent: "")
            qdCli.target = self; aiSub.addItem(qdCli)
        } else {
            let startQd = NSMenuItem(title: "  ▶ Qdrant starten", action: #selector(startQdrant), keyEquivalent: "")
            startQd.target = self; aiSub.addItem(startQd)
        }
        aiSub.addItem(.separator())

        // Quick actions
        let promptItem = NSMenuItem(title: "⚡ AI Prompt  ⌘⇧P", action: #selector(openAiPrompt), keyEquivalent: "p")
        promptItem.target = self; promptItem.keyEquivalentModifierMask = [.command, .shift]; aiSub.addItem(promptItem)
        let netItem = NSMenuItem(title: "� Net Dashboard", action: #selector(openNetDash), keyEquivalent: "")
        netItem.target = self; aiSub.addItem(netItem)

        aiSec.submenu = aiSub
        menu.addItem(aiSec)

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
        let projHome = NSHomeDirectory()
        let projects: [(String, String, String?)] = [
            ("🤖 SuperJarvis", "\(projHome)/projects/SUPERJARVIS", "http://localhost:7777"),
            ("💼 SupersynergyCRM", "\(projHome)/projects/SupersynergyCRM", "http://localhost:8000"),
            ("🕷 ZeroClaw", "\(projHome)/projects/ZeroClawUltimate", nil),
            ("🔍 Omni Scraper", "\(projHome)/projects/omni-scraper", nil),
            ("⚡ AGIMON", "\(projHome)/projects/agimon", nil),
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

        // ── Tools + Dashboard (bottom) ──
        menu.addItem(.separator())

        let toolsItem = styledItem("🛠 Tools", color: Palette.sage)
        let toolsSub = NSMenu()
        let screenshotItem = NSMenuItem(title: "📸 Screenshot → Desktop", action: #selector(captureFullScreenshot), keyEquivalent: "")
        screenshotItem.target = self; toolsSub.addItem(screenshotItem)
        let clipboardGetItem = NSMenuItem(title: "📋 Clipboard anzeigen", action: #selector(showClipboard), keyEquivalent: "")
        clipboardGetItem.target = self; toolsSub.addItem(clipboardGetItem)
        let clipboardClearItem = NSMenuItem(title: "🧹 Clipboard löschen", action: #selector(clearClipboardMenu), keyEquivalent: "")
        clipboardClearItem.target = self; toolsSub.addItem(clipboardClearItem)
        toolsSub.addItem(.separator())
        let watchdogItem = NSMenuItem(title: "🐕 Watchdog Report", action: #selector(showWatchdogReport), keyEquivalent: "")
        watchdogItem.target = self; toolsSub.addItem(watchdogItem)
        toolsItem.submenu = toolsSub
        menu.addItem(toolsItem)

        let dashItem = NSMenuItem(title: "📊 Usage Dashboard", action: #selector(openClaudeStats), keyEquivalent: "u")
        dashItem.target = self; dashItem.keyEquivalentModifierMask = [.command]
        menu.addItem(dashItem)

        menu.addItem(.separator())
        let quitItem = NSMenuItem(title: "❌ Beenden", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quitItem)

        statusItem.menu = menu
    }

    // ── AI Tool Actions ──────────────────────────────────────────
    @objc func openAiPrompt() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/Users/master/.local/bin/ai-prompt")
        try? p.run()
    }
    @objc func openAiSearch() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/Users/master/.local/bin/ai-search")
        try? p.run()
    }
    @objc func openNetDash() {
        let script = """
tell application "Ghostty" to activate
delay 0.2
tell application "System Events"
  tell process "ghostty"
    click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
  end tell
end tell
delay 0.3
tell application "System Events"
  keystroke "net-dashboard"
  key code 36
end tell
"""
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
    }
    @objc func openQdrantCli() {
        let script = """
tell application "Ghostty" to activate
delay 0.2
tell application "System Events"
  tell process "ghostty"
    click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
  end tell
end tell
delay 0.3
tell application "System Events"
  keystroke "qdrant-search"
  key code 36
end tell
"""
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
    }
    @objc func startOllama() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/opt/homebrew/bin/ollama")
        p.arguments = ["serve"]
        try? p.run()
    }
    @objc func startQdrant() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/local/bin/docker")
        p.arguments = ["run", "-d", "-p", "6333:6333", "-p", "6334:6334",
                        "-v", "\(NSHomeDirectory())/qdrant_storage:/qdrant/storage",
                        "qdrant/qdrant"]
        try? p.run()
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
        let claudeBin = "\(NSHomeDirectory())/.local/bin/claude"
        shell("""
            osascript -e 'tell application "Ghostty"
                activate
                set cfg to new surface configuration
                set command of cfg to "\(claudeBin) -r \(sid) --dangerously-skip-permissions"
                new window with configuration cfg
            end tell'
        """)
    }

    @objc func resumeSessionTab(_ sender: NSMenuItem) {
        guard let sid = sender.representedObject as? String else { return }
        resumeSessionInNewTab(sessionId: sid)
    }

    // Helper: Resume session in new tab (used by multiple methods)
    func resumeSessionInNewTab(sessionId: String) {
        // Track this access
        trackSessionAccess(sessionId)

        let claudeBin = "\(NSHomeDirectory())/.local/bin/claude"
        let script = """
tell application "Ghostty"
    activate
end tell
delay 0.2
tell application "System Events"
    tell process "Ghostty"
        click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
    end tell
end tell
delay 0.4
tell application "System Events"
    keystroke "\(claudeBin) -r \(sessionId) --dangerously-skip-permissions"
    key code 36
end tell
"""
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
    }

    // Focus the Ghostty window containing a specific session
    @objc func focusSessionWindow(_ sender: NSMenuItem) {
        guard let sessionId = sender.representedObject as? String else { return }

        // Track access
        trackSessionAccess(sessionId)

        // Find window with this session
        if let gw = lastGhosttyWindows.first(where: { $0.sessionId == sessionId }) {
            focusGhosttyWindow(gw.windowIndex)
        }
    }

    @objc func runOllamaModel(_ sender: NSMenuItem) {
        guard let model = sender.representedObject as? String else { return }
        let script = """
tell application "Ghostty"
    activate
end tell
delay 0.2
tell application "System Events"
    tell process "Ghostty"
        click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
    end tell
end tell
delay 0.4
tell application "System Events"
    keystroke "ollama run \(model)"
    key code 36
end tell
"""
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
    }

    // ── Global Session Search Panel ──────────────────────────────

    var searchPanel: NSPanel?
    var searchField: NSTextField?
    var searchResults: NSTableView?
    var searchData: [SessionInfo] = []
    var filteredSessions: [SessionInfo] = []

    @objc func openSessionSearch() {
        if let existing = searchPanel, existing.isVisible {
            existing.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 640, height: 420),
            styleMask: [.titled, .closable, .resizable, .hudWindow],
            backing: .buffered, defer: false
        )
        panel.title = "🔍 AGIMON — Session Suche"
        panel.center()
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false

        let contentView = NSView(frame: panel.contentView!.bounds)
        contentView.autoresizingMask = [.width, .height]
        panel.contentView = contentView

        // Search field
        let field = NSTextField(frame: NSRect(x: 12, y: 380, width: 616, height: 28))
        field.placeholderString = "Suchen nach Thema, Projekt, Session-ID…"
        field.font = NSFont.systemFont(ofSize: 14)
        field.autoresizingMask = [.width, .minYMargin]
        field.delegate = self
        contentView.addSubview(field)
        self.searchField = field

        // Scrollable table
        let scroll = NSScrollView(frame: NSRect(x: 12, y: 12, width: 616, height: 360))
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true

        let table = NSTableView(frame: scroll.bounds)
        table.autoresizingMask = [.width, .height]
        table.usesAlternatingRowBackgroundColors = true
        table.rowHeight = 46
        table.delegate = self
        table.dataSource = self

        let col1 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("status")); col1.width = 24; col1.title = ""
        let col2 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("msg")); col2.width = 330; col2.title = "Thema"
        let col3 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("project")); col3.width = 110; col3.title = "Projekt"
        let col4 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("time")); col4.width = 60; col4.title = "Zeit"
        let col5 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("msgs")); col5.width = 40; col5.title = "Msg"
        for col in [col1, col2, col3, col4, col5] { table.addTableColumn(col) }

        table.target = self
        table.doubleAction = #selector(searchTableDoubleClick)
        scroll.documentView = table
        contentView.addSubview(scroll)
        self.searchResults = table

        // Populate
        filteredSessions = lastSessions
        searchData = lastSessions
        table.reloadData()

        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        field.becomeFirstResponder()
        self.searchPanel = panel
    }

    // ── Focus Ghostty window by index ───────────────────────────

    @objc func focusWindow(_ sender: NSMenuItem) {
        guard let idx = sender.representedObject as? Int else { return }
        focusGhosttyWindow(idx)
    }

    // ── Full Content Search Panel ────────────────────────────────

    struct ContentHit {
        let sessionId: String
        let message: String      // session first message
        let matchLine: String    // the actual matching line
        let projectName: String
        let modDate: Date
        let windowIndex: Int     // 0 = no window
        let isRunning: Bool
        var score: Int = 0
    }

    var contentPanel: NSPanel?
    var contentField: NSTextField?
    var contentTable: NSTableView?
    var contentHits: [ContentHit] = []
    // Indexed content: sessionId → [user message snippets]
    var contentIndex: [String: [String]] = [:]
    // Track mod-dates to skip unchanged files on incremental rebuild
    var contentIndexModDates: [String: Date] = [:]
    var contentIndexBuilding = false
    var contentSearchWork: DispatchWorkItem?  // for debounce

    func buildContentIndex(force: Bool = false) {
        guard !contentIndexBuilding else { return }
        contentIndexBuilding = true
        let home = NSHomeDirectory()
        let projectsDir = URL(fileURLWithPath: "\(home)/.claude/projects")
        let fm = FileManager.default
        guard let projectDirs = try? fm.contentsOfDirectory(at: projectsDir,
            includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles]) else {
            contentIndexBuilding = false; return
        }

        let cutoff = Date().addingTimeInterval(-30 * 24 * 3600)
        var idx = force ? [:] : contentIndex          // incremental: keep existing
        var modDates = force ? [:] : contentIndexModDates
        var changed = 0

        for dir in projectDirs {
            guard let files = try? fm.contentsOfDirectory(at: dir,
                includingPropertiesForKeys: [.contentModificationDateKey], options: []) else { continue }
            for f in files where f.pathExtension == "jsonl" {
                let sid = f.deletingPathExtension().lastPathComponent
                let mod = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
                guard mod > cutoff else { continue }
                // Skip if already indexed and file hasn't changed
                if !force, let cached = modDates[sid], cached >= mod { continue }

                // Read last 8KB — covers plenty of user messages for search
                var lines: [String] = []
                if let fh = try? FileHandle(forReadingFrom: f) {
                    let size = (try? fh.seekToEnd()) ?? 0
                    let offset = size > 8192 ? size - 8192 : 0
                    try? fh.seek(toOffset: offset)
                    if let str = String(data: fh.readDataToEndOfFile(), encoding: .utf8) {
                        var rawLines = str.components(separatedBy: "\n").filter { !$0.isEmpty }
                        if offset > 0 { rawLines.removeFirst() }
                        for line in rawLines {
                            guard let data = line.data(using: .utf8),
                                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                                  let type_ = obj["type"] as? String, type_ == "user",
                                  let msg = obj["message"] as? [String: Any] else { continue }
                            let c = msg["content"]
                            var text = ""
                            if let s = c as? String { text = s }
                            else if let arr = c as? [[String: Any]] {
                                text = arr.compactMap { $0["text"] as? String }.joined(separator: " ")
                            }
                            let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
                                .replacingOccurrences(of: "\n", with: " ")
                            if clean.count > 3 { lines.append(String(clean.prefix(200))) }
                        }
                    }
                    try? fh.close()
                }
                if !lines.isEmpty { idx[sid] = lines; modDates[sid] = mod; changed += 1 }
            }
        }
        DispatchQueue.main.async { [weak self] in
            self?.contentIndex = idx
            self?.contentIndexModDates = modDates
            self?.contentIndexBuilding = false
        }
    }

    // Professional fuzzy matching with subsequence scoring
    func fuzzyScore(_ query: String, in text: String) -> Int {
        let q = query.lowercased()
        let t = text.lowercased()
        guard !q.isEmpty else { return 0 }

        // Exact match = highest score
        if t == q { return 10000 }
        if t.contains(q) { return 5000 + q.count * 100 }

        // Word boundary matches
        let qWords = q.components(separatedBy: .whitespaces).filter { !$0.isEmpty }
        var wordScore = 0
        for word in qWords {
            if t.contains(word) { wordScore += 500 }
            // Bonus for word prefix match (e.g., "men" matches "menubar")
            let words = t.components(separatedBy: .whitespacesAndNewlines)
            for w in words {
                if w.hasPrefix(word) { wordScore += 300 }
            }
        }

        // Subsequence matching (fuzzy) - find all chars of query in order
        var tIdx = t.startIndex
        var qIdx = q.startIndex
        var subsequenceMatches = 0
        var consecutiveBonus = 0
        var lastMatchIdx: String.Index?

        while tIdx < t.endIndex && qIdx < q.endIndex {
            if t[tIdx] == q[qIdx] {
                subsequenceMatches += 1
                // Consecutive bonus: +50 for each consecutive match
                if let last = lastMatchIdx {
                    let distance = t.distance(from: last, to: tIdx)
                    if distance == 1 { consecutiveBonus += 50 }
                }
                lastMatchIdx = tIdx
                qIdx = q.index(after: qIdx)
            }
            tIdx = t.index(after: tIdx)
        }

        // If not all chars matched, significant penalty
        if qIdx < q.endIndex { return wordScore / 10 }

        // Gap penalty: distance between chars reduces score
        let tLen = t.count
        let gapPenalty = max(0, (tLen - q.count) * 10)

        // Combine scores
        var score = wordScore + subsequenceMatches * 100 + consecutiveBonus - gapPenalty

        // Early match bonus (first char match at start)
        if t.first == q.first { score += 200 }

        return max(0, score)
    }

    func searchContent(_ query: String) -> [ContentHit] {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard q.count >= 2 else { return [] }

        var sidToWin: [String: Int] = [:]
        for gw in lastGhosttyWindows where !gw.sessionId.isEmpty {
            sidToWin[gw.sessionId] = gw.windowIndex
        }
        let sessMap = Dictionary(uniqueKeysWithValues: lastSessions.map { ($0.id, $0) })
        var hits: [ContentHit] = []

        // ── 1. Search Ghostty live buffers first (always instant, always fresh)
        for gw in lastGhosttyWindows where !gw.bufferText.isEmpty {
            let s = fuzzyScore(q, in: gw.bufferText)
            guard s > 0 else { continue }
            let sess = sessMap[gw.sessionId]
            let snippet = String(gw.bufferText.suffix(120))
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .replacingOccurrences(of: "\n", with: " ")
            hits.append(ContentHit(
                sessionId: gw.sessionId,
                message: sess?.message ?? "Win \(gw.windowIndex)",
                matchLine: "🪟 " + String(snippet.prefix(80)),
                projectName: sess?.projectName ?? "",
                modDate: sess?.modDate ?? Date(),
                windowIndex: gw.windowIndex,
                isRunning: true,
                score: s + 300  // Ghostty buffer always boosted — live context
            ))
        }

        // ── 2. Search session history index
        for (sid, lines) in contentIndex {
            var bestLine = ""
            var bestScore = 0
            for line in lines {
                let s = fuzzyScore(q, in: line)
                if s > bestScore { bestScore = s; bestLine = line }
            }
            guard bestScore > 0 else { continue }
            let sess = sessMap[sid]
            var score = bestScore
            if sess?.isRunning == true { score += 200 }
            if sidToWin[sid] != nil { score += 50 }
            hits.append(ContentHit(
                sessionId: sid,
                message: sess?.message ?? String(sid.prefix(12)),
                matchLine: String(bestLine.prefix(80)),
                projectName: sess?.projectName ?? "",
                modDate: sess?.modDate ?? .distantPast,
                windowIndex: sidToWin[sid] ?? 0,
                isRunning: sess?.isRunning ?? false,
                score: score
            ))
        }

        // Deduplicate: if session appears in both buffer+index, keep highest score
        var seen: [String: Int] = [:]  // sid → index in hits
        var deduped: [ContentHit] = []
        for h in hits {
            let key = h.sessionId.isEmpty ? h.matchLine : h.sessionId
            if let existing = seen[key] {
                if h.score > deduped[existing].score { deduped[existing] = h }
            } else {
                seen[key] = deduped.count
                deduped.append(h)
            }
        }
        return deduped.sorted { $0.score > $1.score }.prefix(50).map { $0 }
    }

    @objc func openContentSearch() {
        // Kick off incremental index build in background (non-blocking)
        DispatchQueue.global(qos: .utility).async { [weak self] in
            self?.buildContentIndex()
        }

        if let existing = contentPanel, existing.isVisible {
            existing.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return
        }

        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 720, height: 500),
            styleMask: [.titled, .closable, .resizable, .hudWindow],
            backing: .buffered, defer: false)
        panel.title = "📖 AGIMON — Inhalts-Suche (Fuzzy)"
        panel.center(); panel.level = .floating; panel.isFloatingPanel = true; panel.hidesOnDeactivate = false

        let cv = NSView(frame: panel.contentView!.bounds)
        cv.autoresizingMask = [.width, .height]
        panel.contentView = cv

        let hint = NSTextField(frame: NSRect(x: 12, y: 462, width: 696, height: 18))
        hint.stringValue = "Live: Ghostty-Fenster 🪟 · History: letzte 30 Tage — Enter = fokussieren oder Resume"
        hint.font = NSFont.systemFont(ofSize: 10); hint.textColor = Palette.muted
        hint.isBezeled = false; hint.drawsBackground = false; hint.isEditable = false
        hint.autoresizingMask = [.width, .minYMargin]; cv.addSubview(hint)

        let field = NSTextField(frame: NSRect(x: 12, y: 432, width: 696, height: 28))
        field.placeholderString = "z.B. \"dsgvo scan\" oder \"qdrant index\" oder \"menubar swift\"…"
        field.font = NSFont.systemFont(ofSize: 14)
        field.autoresizingMask = [.width, .minYMargin]
        field.tag = 2  // distinguish from session search field
        field.delegate = self; cv.addSubview(field)
        self.contentField = field

        let scroll = NSScrollView(frame: NSRect(x: 12, y: 12, width: 696, height: 412))
        scroll.autoresizingMask = [.width, .height]; scroll.hasVerticalScroller = true

        let table = NSTableView(frame: scroll.bounds)
        table.autoresizingMask = [.width, .height]; table.usesAlternatingRowBackgroundColors = true
        table.tag = 2  // content table
        table.delegate = self; table.dataSource = self

        let c0 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("ct_status")); c0.width = 24; c0.title = ""
        let c1 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("ct_match")); c1.width = 340; c1.title = "Treffer"
        let c2 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("ct_session")); c2.width = 160; c2.title = "Session"
        let c3 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("ct_proj")); c3.width = 100; c3.title = "Projekt"
        let c4 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("ct_win")); c4.width = 50; c4.title = "Fenster"
        for col in [c0,c1,c2,c3,c4] { table.addTableColumn(col) }

        table.target = self; table.doubleAction = #selector(contentTableDoubleClick)
        scroll.documentView = table; cv.addSubview(scroll)
        self.contentTable = table

        contentHits = []
        table.reloadData()

        panel.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        field.becomeFirstResponder()
        self.contentPanel = panel
    }

    @objc func contentTableDoubleClick() {
        guard let table = contentTable, table.clickedRow >= 0 && table.clickedRow < contentHits.count else { return }
        let hit = contentHits[table.clickedRow]
        contentPanel?.close()
        if hit.windowIndex > 0 {
            focusGhosttyWindow(hit.windowIndex)
        } else {
            // Resume in new tab
            let claudeBin = "\(NSHomeDirectory())/.local/bin/claude"
            let script = """
tell application "Ghostty" to activate
delay 0.2
tell application "System Events"
    tell process "Ghostty"
        click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
    end tell
end tell
delay 0.4
tell application "System Events"
    keystroke "\(claudeBin) -r \(hit.sessionId) --dangerously-skip-permissions"
    key code 36
end tell
"""
            var err: NSDictionary?
            NSAppleScript(source: script)?.executeAndReturnError(&err)
        }
    }

    @objc func searchTableDoubleClick() {
        guard let table = searchResults else { return }
        let row = table.clickedRow
        guard row >= 0 && row < filteredSessions.count else { return }
        let s = filteredSessions[row]
        searchPanel?.close()
        let claudeBin = "\(NSHomeDirectory())/.local/bin/claude"
        let script = """
tell application "Ghostty"
    activate
end tell
delay 0.2
tell application "System Events"
    tell process "Ghostty"
        click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
    end tell
end tell
delay 0.4
tell application "System Events"
    keystroke "\(claudeBin) -r \(s.id) --dangerously-skip-permissions"
    key code 36
end tell
"""
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
    }

    @objc func openTui() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let dir = "\(home)/projects/agimon"
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
            shell("pkill -f 'claude.*--dangerously' || pkill -f 'claude -r'")
        }
    }

    // ═════════════════════════════════════════════════════════════════
    // Ghostty Grid Alignment Selectors
    // ═════════════════════════════════════════════════════════════════

    @objc func arrange2x2() {
        arrangeGhosttyWindows(.twoByTwo)
        updateGhosttyWindowTitles(lastSessions: lastSessions)
    }
    @objc func arrange3x3() {
        arrangeGhosttyWindows(.threeByThree)
        updateGhosttyWindowTitles(lastSessions: lastSessions)
    }
    @objc func arrange4x4() {
        arrangeGhosttyWindows(.fourByFour)
        updateGhosttyWindowTitles(lastSessions: lastSessions)
    }

    @objc func autoArrangeGhostty() {
        // Auto-detect best layout based on window count
        let count = lastGhosttyWindows.count
        let layout: GridLayout
        switch count {
        case 1...4: layout = .twoByTwo
        case 5...6: layout = .custom(cols: 3, rows: 2)
        case 7...9: layout = .threeByThree
        case 10...12: layout = .custom(cols: 4, rows: 3)
        default: layout = .fourByFour
        }
        arrangeGhosttyWindows(layout)
        updateGhosttyWindowTitles(lastSessions: lastSessions)
    }

    @objc func updateGhosttyTitles() {
        updateGhosttyWindowTitles(lastSessions: lastSessions)
    }

    // ═════════════════════════════════════════════════════════════════
    // Best Use Cases: Favorites, Recent, Smart Actions
    // ═════════════════════════════════════════════════════════════════

    // Track session access for recent list
    func trackSessionAccess(_ sessionId: String) {
        var recent = recentSessionIds
        // Remove if exists (to move to front)
        recent.removeAll { $0 == sessionId }
        // Add to front
        recent.insert(sessionId, at: 0)
        recentSessionIds = recent

        // Update access time
        var times = sessionLastAccessed
        times[sessionId] = Date()
        sessionLastAccessed = times
    }

    @objc func pinSession(_ sender: NSMenuItem) {
        guard let sessionId = sender.representedObject as? String else { return }
        var pinned = pinnedSessionIds
        pinned.insert(sessionId)
        pinnedSessionIds = pinned
        rebuildMenu()
    }

    @objc func unpinSession(_ sender: NSMenuItem) {
        guard let sessionId = sender.representedObject as? String else { return }
        var pinned = pinnedSessionIds
        pinned.remove(sessionId)
        pinnedSessionIds = pinned
        rebuildMenu()
    }

    @objc func clearAllRecents() {
        recentSessionIds = []
        sessionLastAccessed = [:]
        rebuildMenu()
    }

    @objc func clearAllPins() {
        pinnedSessionIds = []
        rebuildMenu()
    }

    // Smart sorting: Pinned first, then recent, then by activity
    func smartSortedSessions() -> [SessionInfo] {
        let pinned = pinnedSessionIds
        let recent = Set(recentSessionIds)

        return lastSessions.sorted { a, b in
            let aPinned = pinned.contains(a.id)
            let bPinned = pinned.contains(b.id)
            let aRecent = recent.contains(a.id)
            let bRecent = recent.contains(b.id)

            // Pinned always first
            if aPinned && !bPinned { return true }
            if !aPinned && bPinned { return false }

            // Recent before non-recent (if both pinned or both not pinned)
            if aRecent && !bRecent { return true }
            if !aRecent && bRecent { return false }

            // Finally sort by modification date
            return a.modDate > b.modDate
        }
    }

    // Get session display priority (for visual indicators)
    func sessionPriority(_ session: SessionInfo) -> (isPinned: Bool, isRecent: Bool, accessCount: Int) {
        let pinned = pinnedSessionIds.contains(session.id)
        let recent = recentSessionIds.contains(session.id)
        let count = sessionLastAccessed[session.id] != nil ? 1 : 0
        return (pinned, recent, count)
    }

    // Batch: Kill all non-pinned sessions
    @objc func killNonPinnedSessions() {
        let pinned = pinnedSessionIds
        let toKill = lastSessions.filter { !pinned.contains($0.id) && $0.isRunning }

        let alert = NSAlert()
        alert.messageText = "\(toKill.count) nicht-gepinnte Sessions stoppen?"
        alert.informativeText = "Gepinnte Sessions bleiben erhalten."
        alert.addButton(withTitle: "Stoppen")
        alert.addButton(withTitle: "Abbrechen")

        if alert.runModal() == .alertFirstButtonReturn {
            for session in toKill {
                if !session.runningPid.isEmpty {
                    shell("kill \(session.runningPid) 2>/dev/null")
                }
            }
            // Refresh after delay
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.triggerBackgroundFetch()
            }
        }
    }

    // Batch: Resume all pinned sessions in new tabs
    @objc func resumeAllPinned() {
        let pinned = pinnedSessionIds
        let toResume = lastSessions.filter { pinned.contains($0.id) && !$0.isRunning }

        for (idx, session) in toResume.enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(idx) * 0.5) { [weak self] in
                self?.resumeSessionInNewTab(sessionId: session.id)
            }
        }
    }

    // Build project-centric view
    func buildProjectGroups() -> [(name: String, path: String, sessions: [SessionInfo])] {
        let grouped = Dictionary(grouping: lastSessions) { $0.projectName }
        return grouped.map { (name: $0.key, path: $0.value.first?.cwd ?? "", sessions: $0.value) }
            .sorted { $0.sessions.count > $1.sessions.count }
    }

    // ═════════════════════════════════════════════════════════════════
    // Universal Window Manager Selectors
    // ═════════════════════════════════════════════════════════════════

    @objc func gatherAllWindows() {
        // Bring all windows from all apps to current space
        let apps = getWindowsByApp().map { $0.app }
        for app in apps {
            gatherAppWindows(app)
        }
    }

    @objc func arrangeAllApps() {
        // Auto-arrange windows for all apps with multiple windows
        let apps = getWindowsByApp().filter { $0.windows.count > 1 }
        for (appName, windows) in apps {
            let layout: GridLayout
            switch windows.count {
            case 1...4: layout = .twoByTwo
            case 5...6: layout = .custom(cols: 3, rows: 2)
            case 7...9: layout = .threeByThree
            default: layout = .fourByFour
            }
            arrangeAppWindows(appName, layout: layout)
        }
    }

    @objc func arrangeSpecificApp(_ sender: NSMenuItem) {
        guard let dict = sender.representedObject as? [String: String],
              let appName = dict["app"],
              let layoutStr = dict["layout"] else { return }

        let layout: GridLayout
        switch layoutStr {
        case "2x2": layout = .twoByTwo
        case "3x3": layout = .threeByThree
        case "4x4": layout = .fourByFour
        default: layout = .twoByTwo
        }
        arrangeAppWindows(appName, layout: layout)
    }

    @objc func cascadeSpecificApp(_ sender: NSMenuItem) {
        guard let appName = sender.representedObject as? String else { return }
        cascadeAppWindows(appName)
    }

    @objc func gatherSpecificApp(_ sender: NSMenuItem) {
        guard let appName = sender.representedObject as? String else { return }
        gatherAppWindows(appName)
    }

    @objc func focusManagedWindow(_ sender: NSMenuItem) {
        guard let dict = sender.representedObject as? [String: Any],
              let pidNum = dict["pid"] as? NSNumber else { return }
        let pid = pidNum.int32Value

        // Find and activate the window - use processIdentifier directly
        let apps = NSWorkspace.shared.runningApplications
        if let app = apps.first(where: { $0.processIdentifier == pid }) {
            app.activate(options: NSApplication.ActivationOptions.activateAllWindows)
        }
    }

    // ═════════════════════════════════════════════════════════════════
    // Agent-Desktop Inspired Tools: Screenshot, Clipboard, Wait
    // ═════════════════════════════════════════════════════════════════

    @objc func captureFullScreenshot() {
        // Capture entire screen
        if let image = captureScreenshot() {
            let desktopPath = "\(NSHomeDirectory())/Desktop/agimon-sccreenshot-\(Int(Date().timeIntervalSince1970)).png"
            if saveScreenshot(image, to: desktopPath) {
                // Show success notification
                let notification = NSUserNotification()
                notification.title = "📸 Screenshot gespeichert"
                notification.informativeText = "Auf Desktop: \(URL(fileURLWithPath: desktopPath).lastPathComponent)"
                NSUserNotificationCenter.default.deliver(notification)
            }
        }
    }

    @objc func showClipboard() {
        let content = clipboardGet()
        let alert = NSAlert()
        alert.messageText = "📋 Clipboard Inhalt"
        alert.informativeText = content.isEmpty ? "(leer)" : String(content.prefix(500))
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Kopieren & Schließen")

        let response = alert.runModal()
        if response == .alertSecondButtonReturn {
            // Copy to clipboard again (confirms it's there)
            clipboardSet(content)
        }
    }

    @objc func clearClipboardMenu() {
        clipboardClear()

        let notification = NSUserNotification()
        notification.title = "🧹 Clipboard gelöscht"
        notification.informativeText = "Zwischenablage wurde geleert"
        NSUserNotificationCenter.default.deliver(notification)
    }

    // 🎯 Focus Mode: Kill idle Claude processes, then arrange active in grid
    @objc func activateFocusMode() {
        let running = lastSessions.filter { $0.isRunning }
        let idle    = lastSessions.filter { !$0.isRunning && !$0.runningPid.isEmpty }

        let alert = NSAlert()
        alert.messageText = "🎯 Focus Mode aktivieren?"
        alert.informativeText = "\(idle.count) idle Prozesse stoppen · \(running.count) aktive in Grid arrangieren"
        alert.addButton(withTitle: "Aktivieren")
        alert.addButton(withTitle: "Abbrechen")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        // Kill idle
        for s in idle {
            shell("kill \(s.runningPid) 2>/dev/null")
        }

        // Arrange active Ghostty windows in best-fit grid
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in
            guard let self = self else { return }
            let count = self.lastGhosttyWindows.count
            if      count <= 2  { self.arrange2x1() }
            else if count <= 4  { self.arrange2x2() }
            else if count <= 6  { self.arrange3x2() }
            else if count <= 9  { self.arrange3x3() }
            else                { self.arrange4x4() }

            sendNotification(title: "🎯 Focus Mode aktiv",
                body: "\(running.count) Sessions in Grid · \(idle.count) gestoppt")

            self.triggerBackgroundFetch()
        }
    }

    // 2×1 Layout (side-by-side, 2 windows)
    @objc func arrange2x1() { arrangeGhosttyWindows(.twoByOne) }

    // 3×2 Layout (6 windows)
    @objc func arrange3x2() { arrangeGhosttyWindows(.threeByTwo) }

    // 🏷️ Rename/label a session
    @objc func renameSession(_ sender: NSMenuItem) {
        guard let sessionId = sender.representedObject as? String else { return }
        let session = lastSessions.first { $0.id == sessionId }

        let alert = NSAlert()
        alert.messageText = "🏷️ Session Label"
        alert.informativeText = "Kurzer Name für diese Session:"
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        input.stringValue = sessionLabels[sessionId] ?? session?.message ?? ""
        input.placeholderString = "z.B. 'Payment API Fix'"
        alert.accessoryView = input
        alert.addButton(withTitle: "Speichern")
        alert.addButton(withTitle: "Label löschen")
        alert.addButton(withTitle: "Abbrechen")

        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            var labels = sessionLabels
            labels[sessionId] = input.stringValue.isEmpty ? nil : input.stringValue
            sessionLabels = labels
            rebuildMenu()
        } else if response == .alertSecondButtonReturn {
            var labels = sessionLabels
            labels.removeValue(forKey: sessionId)
            sessionLabels = labels
            rebuildMenu()
        }
    }

    // 🐕 Watchdog health report
    @objc func showWatchdogReport() {
        let runningSessions = lastSessions.filter { $0.isRunning }
        let pinnedCount = pinnedSessionIds.count
        let recentCount = recentSessionIds.count
        let totalSessions = lastSessions.count
        let burnRate = currentBurnRate().map { String(format: "$%.2f/h", $0) } ?? "n/a"

        let report = """
        🐕 AGIMON Health Report  ·  \(Date().formatted(date: .abbreviated, time: .shortened))

        Sessions
         • Gesamt:    \(totalSessions)
         • Laufend:   \(runningSessions.count)
         • Gepinnt:   \(pinnedCount)
         • Zuletzt:   \(recentCount)

        Kosten
         • Heute:     \(lastUsage.map { "$\(String(format: "%.2f", $0.todayCost))" } ?? "n/a")
         • Burn Rate: \(burnRate)

        Rate Limits
         • 5h-Limit:  \(lastRateLimit.map { "\(Int($0.fiveHour))%" } ?? "n/a")
         • 7d-Limit:  \(lastRateLimit.map { "\(Int($0.sevenDay))%" } ?? "n/a")

        Fenster
         • Ghostty:   \(lastGhosttyWindows.count)
         • Alle Apps: \(lastAllWindowsByApp.reduce(0) { $0 + $1.1.count })
        """

        let alert = NSAlert()
        alert.messageText = "🐕 AGIMON Watchdog"
        alert.informativeText = report
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "📋 Kopieren")
        if alert.runModal() == .alertSecondButtonReturn {
            clipboardSet(report)
        }
    }

    // Wait for condition with timeout (agent-desktop inspired)
    func waitFor(_ condition: WaitCondition, timeoutMs: Int = 5000, completion: @escaping (Bool) -> Void) {
        let startTime = Date()

        switch condition {
        case .time(let ms):
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(ms) / 1000.0) {
                completion(true)
            }

        case .window(let title, let app):
            // Poll for window appearance
            Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] timer in
                guard let self = self else { timer.invalidate(); return }
                let elapsed = Int(Date().timeIntervalSince(startTime) * 1000)
                if elapsed >= timeoutMs {
                    timer.invalidate()
                    completion(false)
                    return
                }

                let windows = getAllManagedWindows()
                let found = windows.contains { win in
                    let titleMatch = win.title.contains(title)
                    let appMatch = app == nil || win.appName.lowercased().contains(app!.lowercased())
                    return titleMatch && appMatch
                }

                if found {
                    timer.invalidate()
                    completion(true)
                }
            }

        case .text(let content, let app):
            // Poll for text in buffers
            Timer.scheduledTimer(withTimeInterval: 0.3, repeats: true) { [weak self] timer in
                guard let self = self else { timer.invalidate(); return }
                let elapsed = Int(Date().timeIntervalSince(startTime) * 1000)
                if elapsed >= timeoutMs {
                    timer.invalidate()
                    completion(false)
                    return
                }

                // Check Ghostty buffers
                let found = self.lastGhosttyWindows.contains { gw in
                    let appMatch = app == nil || gw.bufferText.contains(content)
                    return appMatch
                }

                if found {
                    timer.invalidate()
                    completion(true)
                }
            }
        }
    }

    @objc func autoFix(_ sender: NSMenuItem) {
        guard let cmd = sender.representedObject as? String else { return }
        // Run detached — don't block main thread
        let p = Process()
        p.launchPath = "/bin/sh"
        p.arguments = ["-c", cmd]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
        // Refresh data after 3s to pick up service status changes
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.triggerBackgroundFetch()
        }
    }

    @objc func refreshAllWindows() {
        DispatchQueue.global(qos: .userInitiated).async {
            let byApp = getWindowsByApp()
            DispatchQueue.main.async {
                self.lastAllWindowsByApp = byApp
                self.rebuildMenu()
            }
        }
    }

    @objc func manualRefresh() {
        rateLimitFetchTick = 0  // force rate limit re-fetch
        ghosttyFetchTick = 0    // force Ghostty window re-fetch
        triggerBackgroundFetch()
    }

    @objc func focusWindowForPid(_ sender: NSMenuItem) {
        guard let pidStr = sender.representedObject as? String else { return }
        // Find which Ghostty window has this PID on its TTY
        let ttyRaw = shell("ps -o tty= -p \(pidStr) 2>/dev/null").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !ttyRaw.isEmpty, ttyRaw != "??" else { return }
        // Match TTY to window index via lastGhosttyWindows
        if let gw = lastGhosttyWindows.first(where: { $0.tty == ttyRaw }) {
            focusGhosttyWindow(gw.windowIndex)
        }
    }

    var statsPanel: NSPanel?

    @objc func openClaudeStats() {
        if let existing = statsPanel, existing.isVisible {
            existing.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return
        }
        // Use empty defaults if data not yet loaded
        let u = lastUsage ?? ClaudeUsage(todayCost: 0, todayTokens: 0, yesterdayCost: 0,
            weekCost: 0, weekTokens: 0, allTimeCost: 0, days: 0, modelsToday: [], plan: "?",
            dailyHistory: [], monthCost: 0, cacheReadTokens: 0, cacheCreationTokens: 0)
        guard let d = lastData else { return }

        let W: CGFloat = 580, H: CGFloat = 560
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: W, height: H),
            styleMask: [.titled, .closable, .hudWindow],
            backing: .buffered, defer: false)
        panel.title = "Claude Code · Usage Dashboard"
        panel.center(); panel.level = .floating; panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        statsPanel = panel

        let cv = NSView(frame: NSRect(x: 0, y: 0, width: W, height: H))
        panel.contentView = cv

        // ── Background
        let bg = NSBox(frame: cv.bounds)
        bg.boxType = .custom; bg.fillColor = NSColor(white: 0.10, alpha: 1)
        bg.borderColor = NSColor(white: 0.20, alpha: 1); bg.borderWidth = 1; bg.cornerRadius = 0
        cv.addSubview(bg)

        var y: CGFloat = H - 20

        func label(_ text: String, x: CGFloat, y: CGFloat, w: CGFloat, h: CGFloat = 20,
                   size: CGFloat = 12, color: NSColor = .white, bold: Bool = false, mono: Bool = false) {
            let tf = NSTextField(frame: NSRect(x: x, y: y, width: w, height: h))
            tf.stringValue = text
            tf.font = mono ? NSFont.monospacedSystemFont(ofSize: size, weight: bold ? .bold : .regular)
                           : NSFont.systemFont(ofSize: size, weight: bold ? .semibold : .regular)
            tf.textColor = color; tf.isBezeled = false; tf.drawsBackground = false; tf.isEditable = false
            cv.addSubview(tf)
        }

        // ── Title row
        y -= 18
        let planColor: NSColor = u.plan == "Max" ? NSColor(calibratedRed: 1, green: 0.80, blue: 0.20, alpha: 1)
                                                  : NSColor(calibratedRed: 0.40, green: 0.65, blue: 1, alpha: 1)
        let planIcon = u.plan == "Max" ? "⭐" : (u.plan == "Pro" ? "🔵" : "🔑")
        label("\(planIcon) Claude \(u.plan) — Usage Dashboard", x: 20, y: y, w: 350, size: 15, color: planColor, bold: true)
        label("API pricing · \(u.days) Tage", x: W - 140, y: y + 2, w: 120, size: 10,
              color: NSColor(white: 0.38, alpha: 1))
        y -= 16
        if !u.modelsToday.isEmpty {
            label("Heute: " + u.modelsToday.joined(separator: " · "), x: 20, y: y, w: W - 40, size: 10,
                  color: NSColor(white: 0.45, alpha: 1), mono: true)
        }
        y -= 4

        // ── Divider
        let sep1 = NSBox(frame: NSRect(x: 20, y: y, width: W - 40, height: 1))
        sep1.boxType = .separator; cv.addSubview(sep1)
        y -= 24

        // ── Stat cards row
        func statCard(_ title: String, _ value: String, _ sub: String, x: CGFloat, cardW: CGFloat = 120,
                      valueColor: NSColor = NSColor(calibratedRed: 0.30, green: 0.85, blue: 0.50, alpha: 1)) {
            let box = NSBox(frame: NSRect(x: x, y: y - 52, width: cardW, height: 56))
            box.boxType = .custom
            box.fillColor = NSColor(white: 0.16, alpha: 1)
            box.borderColor = NSColor(white: 0.25, alpha: 1); box.borderWidth = 1; box.cornerRadius = 8
            cv.addSubview(box)
            label(title, x: x + 10, y: y - 16, w: cardW - 20, size: 10,
                  color: NSColor(white: 0.50, alpha: 1), bold: false)
            label(value, x: x + 10, y: y - 34, w: cardW - 20, size: 16, color: valueColor, bold: true, mono: true)
            label(sub, x: x + 10, y: y - 50, w: cardW - 20, size: 10,
                  color: NSColor(white: 0.40, alpha: 1), mono: true)
        }

        let green  = NSColor(calibratedRed: 0.30, green: 0.85, blue: 0.50, alpha: 1)
        let yellow = NSColor(calibratedRed: 1.00, green: 0.78, blue: 0.30, alpha: 1)
        let blue   = NSColor(calibratedRed: 0.40, green: 0.65, blue: 1.00, alpha: 1)
        let purple = NSColor(calibratedRed: 0.70, green: 0.50, blue: 0.95, alpha: 1)

        let orange = NSColor(calibratedRed: 1.00, green: 0.55, blue: 0.20, alpha: 1)
        let cardW: CGFloat = (W - 40) / 5 - 4
        statCard("HEUTE", "$\(String(format: "%.2f", u.todayCost))",
                 formatTokens(u.todayTokens), x: 20, cardW: cardW, valueColor: green)
        statCard("GESTERN", "$\(String(format: "%.2f", u.yesterdayCost))",
                 u.yesterdayCost > u.todayCost ? "↑ teurer" : "↓ günstiger",
                 x: 20 + (cardW + 4) * 1, cardW: cardW, valueColor: yellow)
        statCard("7 TAGE", "$\(String(format: "%.0f", u.weekCost))",
                 formatTokens(u.weekTokens), x: 20 + (cardW + 4) * 2, cardW: cardW, valueColor: blue)
        statCard("MONAT", "$\(String(format: "%.0f", u.monthCost))",
                 Calendar.current.monthSymbols[Calendar.current.component(.month, from: Date()) - 1],
                 x: 20 + (cardW + 4) * 3, cardW: cardW, valueColor: orange)
        statCard("ALL-TIME", "$\(String(format: "%.0f", u.allTimeCost))",
                 "\(u.days) Tage", x: 20 + (cardW + 4) * 4, cardW: cardW, valueColor: purple)
        y -= 68

        // ── Budget progress bar
        let b = d.budget
        if b.budget > 0 {
            y -= 10
            let pct = min(b.spent / b.budget, 1.0)
            label("API Budget  $\(String(format: "%.2f", b.spent)) / $\(String(format: "%.0f", b.budget))",
                  x: 20, y: y, w: 300, size: 11, color: NSColor(white: 0.65, alpha: 1), bold: true)
            let pctStr = String(format: "%.0f%%", b.spent / b.budget * 100)
            label(pctStr, x: W - 65, y: y, w: 50, size: 11,
                  color: pct > 0.8 ? yellow : green, bold: true, mono: true)
            y -= 18
            // Track background
            let trackBg = NSBox(frame: NSRect(x: 20, y: y, width: W - 40, height: 10))
            trackBg.boxType = .custom; trackBg.fillColor = NSColor(white: 0.22, alpha: 1)
            trackBg.borderWidth = 0; trackBg.cornerRadius = 5; cv.addSubview(trackBg)
            // Fill
            let fillW = max(6, (W - 40) * CGFloat(pct))
            let fillColor: NSColor = pct > 1 ? NSColor(calibratedRed: 1, green: 0.3, blue: 0.3, alpha: 1)
                                  : pct > 0.8 ? yellow : green
            let fill = NSBox(frame: NSRect(x: 20, y: y, width: fillW, height: 10))
            fill.boxType = .custom; fill.fillColor = fillColor
            fill.borderWidth = 0; fill.cornerRadius = 5; cv.addSubview(fill)
            y -= 22
        }

        // ── 30-day cost sparkline (from ccusage history)
        let dayCosts = u.dailyHistory  // (date, cost, tokens)
        if !dayCosts.isEmpty {
            y -= 10
            let dayLabel = dayCosts.count > 7 ? "letzte \(dayCosts.count) Tage" : "letzte 7 Tage"
            label("Verlauf (\(dayLabel))", x: 20, y: y, w: 220, size: 11,
                  color: NSColor(white: 0.55, alpha: 1), bold: true)
            // avg/day
            let avg = dayCosts.map { $0.cost }.reduce(0,+) / Double(dayCosts.count)
            label(String(format: "⌀ $%.0f/Tag", avg), x: W - 110, y: y, w: 90, size: 10,
                  color: NSColor(white: 0.40, alpha: 1), mono: true)
            y -= 100

            let maxCost = dayCosts.map { $0.cost }.max() ?? 1.0
            let barAreaW = W - 40
            let barCount = CGFloat(dayCosts.count)
            let barW = min(18, (barAreaW / barCount) * 0.70)
            let gapW = (barAreaW - barW * barCount) / barCount
            let barMaxH: CGFloat = 80

            for (i, rec) in dayCosts.enumerated() {
                let barH = max(2, CGFloat(rec.cost / maxCost) * barMaxH)
                let bx = 20 + CGFloat(i) * (barW + gapW) + gapW / 2
                let isToday = i == dayCosts.count - 1
                let isWeekend: Bool = {
                    let df2 = DateFormatter(); df2.dateFormat = "MM-dd"
                    let yr = Calendar.current.component(.year, from: Date())
                    if let d2 = df2.date(from: rec.date) {
                        var comps = Calendar.current.dateComponents([.month,.day], from: d2)
                        comps.year = yr
                        if let fd = Calendar.current.date(from: comps) {
                            let wd = Calendar.current.component(.weekday, from: fd)
                            return wd == 1 || wd == 7
                        }
                    }
                    return false
                }()

                let barColor: NSColor = isToday ? blue
                    : rec.cost > avg * 2 ? NSColor(calibratedRed: 1.0, green: 0.50, blue: 0.20, alpha: 1)
                    : isWeekend ? NSColor(white: 0.28, alpha: 1)
                    : NSColor(white: 0.38, alpha: 1)

                let barBox = NSBox(frame: NSRect(x: bx, y: y, width: barW, height: barH))
                barBox.boxType = .custom; barBox.fillColor = barColor
                barBox.borderWidth = 0; barBox.cornerRadius = 2; cv.addSubview(barBox)

                // Cost label above bar (only show for bars with room)
                if rec.cost > 0 && barW >= 8 {
                    let costLbl = NSTextField(frame: NSRect(x: bx - 4, y: y + barH + 2, width: barW + 12, height: 13))
                    costLbl.stringValue = "$\(String(format: "%.0f", rec.cost))"
                    costLbl.font = NSFont.monospacedSystemFont(ofSize: 8, weight: .regular)
                    costLbl.textColor = isToday ? blue : NSColor(white: 0.45, alpha: 1)
                    costLbl.isBezeled = false; costLbl.drawsBackground = false; costLbl.isEditable = false
                    costLbl.alignment = .center; cv.addSubview(costLbl)
                }

                // Date label below — show only weekly ticks to avoid clutter
                if isToday || i % max(1, dayCosts.count / 7) == 0 {
                    let dateLbl = NSTextField(frame: NSRect(x: bx - 4, y: y - 15, width: barW + 12, height: 13))
                    dateLbl.stringValue = isToday ? "now" : rec.date
                    dateLbl.font = NSFont.monospacedSystemFont(ofSize: 8, weight: .regular)
                    dateLbl.textColor = isToday ? blue : NSColor(white: 0.35, alpha: 1)
                    dateLbl.isBezeled = false; dateLbl.drawsBackground = false; dateLbl.isEditable = false
                    dateLbl.alignment = .center; cv.addSubview(dateLbl)
                }
            }
            y -= 30
        }

        // ── Rate Limit bars (if available)
        if let rl = lastRateLimit {
            y -= 4
            let sep2 = NSBox(frame: NSRect(x: 20, y: y, width: W - 40, height: 1))
            sep2.boxType = .separator; cv.addSubview(sep2)
            y -= 20

            label("RATE LIMITS  (Claude \(rl.plan) · \(rl.cacheAge == 0 ? "live" : "\(rl.cacheAge)s cached"))",
                  x: 20, y: y, w: W - 40, size: 10, color: NSColor(white: 0.45, alpha: 1), bold: true)
            y -= 18

            let rlBars: [(String, Double, String)] = {
                var bars: [(String, Double, String)] = []
                bars.append(("5h",  rl.fiveHour,
                    rl.fiveHourResetsIn > 0 ? "+\(rl.fiveHourResetsIn)min" : ""))
                bars.append(("7d",  rl.sevenDay,
                    rl.sevenDayResetsIn > 0 ? "+\(rl.sevenDayResetsIn)h" : ""))
                if let son = rl.sevenDaySonnet { bars.append(("son", son, "7d Sonnet")) }
                // Only show extra credits bar if there's a meaningful limit
                if let ep = rl.extraPct, let eu = rl.extraUsed, let el = rl.extraLimit, el > 0 {
                    bars.append(("extra", ep, "$\(Int(eu))/$\(Int(el))"))
                }
                return bars
            }()
            let rlBarW: CGFloat = W - 160
            for (lbl, pct, resetStr) in rlBars {
                let barColor: NSColor = pct > 90
                    ? NSColor(calibratedRed: 1.0, green: 0.30, blue: 0.30, alpha: 1)
                    : pct > 60
                        ? NSColor(calibratedRed: 1.0, green: 0.75, blue: 0.20, alpha: 1)
                        : NSColor(calibratedRed: 0.30, green: 0.85, blue: 0.50, alpha: 1)
                label(lbl, x: 20, y: y, w: 45, size: 11, color: NSColor(white: 0.55, alpha: 1), mono: true)
                let trackBox = NSBox(frame: NSRect(x: 68, y: y + 2, width: rlBarW, height: 10))
                trackBox.boxType = .custom; trackBox.fillColor = NSColor(white: 0.22, alpha: 1)
                trackBox.borderWidth = 0; trackBox.cornerRadius = 4; cv.addSubview(trackBox)
                let fillW = max(4, CGFloat(pct) / 100 * rlBarW)
                let fillBox = NSBox(frame: NSRect(x: 68, y: y + 2, width: fillW, height: 10))
                fillBox.boxType = .custom; fillBox.fillColor = barColor
                fillBox.borderWidth = 0; fillBox.cornerRadius = 4; cv.addSubview(fillBox)
                label(String(format: "%3.0f%%", pct), x: 68 + rlBarW + 6, y: y, w: 40, size: 11,
                      color: barColor, bold: true, mono: true)
                label(resetStr, x: 68 + rlBarW + 50, y: y, w: 90, size: 10,
                      color: NSColor(white: 0.38, alpha: 1), mono: true)
                y -= 16
            }
        }

        // ── Running sessions summary
        let sep3 = NSBox(frame: NSRect(x: 20, y: y, width: W - 40, height: 1))
        sep3.boxType = .separator; cv.addSubview(sep3)
        y -= 20

        let runCount = lastSessions.filter { $0.isRunning }.count
        let todayCount = lastSessions.filter { Calendar.current.isDateInToday($0.modDate) }.count
        let sessionSummary = "🟢 \(runCount) laufend  ·  📅 \(todayCount) heute  ·  📊 \(lastSessions.count) gesamt"
        label(sessionSummary, x: 20, y: y, w: W - 40, size: 11,
              color: NSColor(white: 0.60, alpha: 1))
        y -= 18

        // Cache efficiency from ccusage data
        let cacheR = u.cacheReadTokens
        let cacheC = u.cacheCreationTokens
        if cacheR > 0 || cacheC > 0 {
            let total = max(u.todayTokens, 1)
            let pctCache = Int(Double(cacheR) / Double(total) * 100)
            label("Cache-Read: \(formatTokens(cacheR)) (\(pctCache)%)  ·  Cache-Write: \(formatTokens(cacheC))",
                  x: 20, y: y, w: W - 40, size: 10, color: NSColor(white: 0.42, alpha: 1), mono: true)
            y -= 16
        }

        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

// ── Search Panel: NSTableView + NSTextField Delegates ───────────

extension AgimonDelegate: NSTableViewDataSource, NSTableViewDelegate {
    func numberOfRows(in tableView: NSTableView) -> Int {
        return tableView.tag == 2 ? contentHits.count : filteredSessions.count
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let colId = tableColumn?.identifier.rawValue ?? ""
        let reuseId = NSUserInterfaceItemIdentifier(colId)
        let cell: NSTextField
        if let recycled = tableView.makeView(withIdentifier: reuseId, owner: nil) as? NSTextField {
            cell = recycled
        } else {
            cell = NSTextField(frame: .zero)
            cell.identifier = reuseId
            cell.isBezeled = false; cell.drawsBackground = false; cell.isEditable = false
            cell.lineBreakMode = .byTruncatingTail; cell.maximumNumberOfLines = 1
        }

        if tableView.tag == 2 {
            // Content search table
            guard row < contentHits.count else { return nil }
            let h = contentHits[row]
            cell.font = colId == "ct_match" || colId == "ct_session" ?
                NSFont.systemFont(ofSize: 12) : NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
            switch colId {
            case "ct_status":
                cell.stringValue = h.isRunning ? "🟢" : (h.windowIndex > 0 ? "🪟" : "○")
            case "ct_match":
                cell.stringValue = h.matchLine
                cell.textColor = h.isRunning ? Palette.alive : NSColor.labelColor
            case "ct_session":
                cell.stringValue = h.message
                cell.textColor = Palette.amber
            case "ct_proj":
                cell.stringValue = h.projectName.isEmpty ? "—" : h.projectName
                cell.textColor = Palette.teal
            case "ct_win":
                cell.stringValue = h.windowIndex > 0 ? "Win \(h.windowIndex)" : "resume"
                cell.textColor = h.windowIndex > 0 ? Palette.violet : Palette.muted
            default: break
            }
        } else {
            // Session search table
            guard row < filteredSessions.count else { return nil }
            let s = filteredSessions[row]
            cell.font = colId == "msg" ? NSFont.systemFont(ofSize: 12) : NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
            switch colId {
            case "status":
                cell.stringValue = s.isRunning ? "🟢" : (s.active ? "🟡" : "○")
            case "msg":
                cell.stringValue = s.message
                cell.textColor = s.isRunning ? Palette.alive : NSColor.labelColor
            case "project":
                cell.stringValue = s.projectName.isEmpty ? "—" : s.projectName
                cell.textColor = Palette.teal
            case "time":
                cell.stringValue = s.timeAgo
                cell.textColor = Palette.muted
            case "msgs":
                cell.stringValue = "\(s.msgCount)"
                cell.textColor = Palette.muted
            default: break
            }
        }
        return cell
    }

    func tableView(_ tableView: NSTableView, heightOfRow row: Int) -> CGFloat { 36 }
}

extension AgimonDelegate: NSTextFieldDelegate {
    func controlTextDidChange(_ obj: Notification) {
        guard let field = obj.object as? NSTextField else { return }
        if field === contentField || field.tag == 2 {
            // 150ms debounce: cancel previous work item before starting new one
            contentSearchWork?.cancel()
            let query = field.stringValue
            let work = DispatchWorkItem { [weak self] in
                guard let self = self else { return }
                let hits = self.searchContent(query)
                DispatchQueue.main.async {
                    self.contentHits = hits
                    self.contentTable?.reloadData()
                }
            }
            contentSearchWork = work
            DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 0.15, execute: work)
        } else if field === searchField {
            let query = field.stringValue.lowercased().trimmingCharacters(in: .whitespaces)
            if query.isEmpty {
                filteredSessions = searchData
            } else {
                filteredSessions = searchData.filter {
                    $0.message.lowercased().contains(query) ||
                    $0.projectName.lowercased().contains(query) ||
                    $0.id.lowercased().hasPrefix(query) ||
                    $0.cwd.lowercased().contains(query)
                }
            }
            searchResults?.reloadData()
        }
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        let selName = NSStringFromSelector(commandSelector)
        // Content search panel keyboard nav
        if control === contentField || (control as? NSTextField)?.tag == 2 {
            if selName == "insertNewline:" {
                guard let table = contentTable, table.selectedRow >= 0 && table.selectedRow < contentHits.count else { return false }
                let hit = contentHits[table.selectedRow]
                contentPanel?.close()
                if hit.windowIndex > 0 {
                    focusGhosttyWindow(hit.windowIndex)
                } else {
                    let claudeBin = "\(NSHomeDirectory())/.local/bin/claude"
                    let script = """
tell application "Ghostty" to activate
delay 0.2
tell application "System Events"
    tell process "Ghostty"
        click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
    end tell
end tell
delay 0.4
tell application "System Events"
    keystroke "\(claudeBin) -r \(hit.sessionId) --dangerously-skip-permissions"
    key code 36
end tell
"""
                    var err: NSDictionary?
                    NSAppleScript(source: script)?.executeAndReturnError(&err)
                }
                return true
            }
            if selName == "moveDown:" {
                guard let table = contentTable else { return false }
                let next = min(table.selectedRow + 1, contentHits.count - 1)
                table.selectRowIndexes(IndexSet(integer: next), byExtendingSelection: false)
                table.scrollRowToVisible(next); return true
            }
            if selName == "moveUp:" {
                guard let table = contentTable else { return false }
                let prev = max(table.selectedRow - 1, 0)
                table.selectRowIndexes(IndexSet(integer: prev), byExtendingSelection: false)
                table.scrollRowToVisible(prev); return true
            }
            return false
        }
        // Session search panel keyboard nav
        if selName == "insertNewline:" {
            guard let table = searchResults, table.selectedRow >= 0 && table.selectedRow < filteredSessions.count else { return false }
            let s = filteredSessions[table.selectedRow]
            searchPanel?.close()
            let claudeBin = "\(NSHomeDirectory())/.local/bin/claude"
            let script = """
tell application "Ghostty"
    activate
end tell
delay 0.2
tell application "System Events"
    tell process "Ghostty"
        click menu item "New Tab" of menu "File" of menu bar item "File" of menu bar 1
    end tell
end tell
delay 0.4
tell application "System Events"
    keystroke "\(claudeBin) -r \(s.id) --dangerously-skip-permissions"
    key code 36
end tell
"""
            var err: NSDictionary?
            NSAppleScript(source: script)?.executeAndReturnError(&err)
            return true
        }
        if selName == "moveDown:" {
            guard let table = searchResults else { return false }
            let next = min(table.selectedRow + 1, filteredSessions.count - 1)
            table.selectRowIndexes(IndexSet(integer: next), byExtendingSelection: false)
            table.scrollRowToVisible(next)
            return true
        }
        if selName == "moveUp:" {
            guard let table = searchResults else { return false }
            let prev = max(table.selectedRow - 1, 0)
            table.selectRowIndexes(IndexSet(integer: prev), byExtendingSelection: false)
            table.scrollRowToVisible(prev)
            return true
        }
        return false
    }
}

// ── Main ────────────────────────────────────────────────────────

// Single-instance guard: kill any existing agimon-menu before starting
let selfPid = ProcessInfo.processInfo.processIdentifier
let existingPids = shell("pgrep -x agimon-menu 2>/dev/null")
    .split(separator: "\n").compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
    .filter { $0 != selfPid }
for pid in existingPids {
    kill(pid_t(pid), SIGTERM)
}
if !existingPids.isEmpty { Thread.sleep(forTimeInterval: 0.3) }

let app = NSApplication.shared
app.setActivationPolicy(.accessory) // no dock icon
let delegate = AgimonDelegate()
app.delegate = delegate
app.run()
