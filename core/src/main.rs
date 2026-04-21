//! AGIMON Core — blazing fast process monitor + service health + watchdog
//! Replaces Python subprocess calls with native sysinfo (100x faster)

use clap::{Parser, Subcommand};
use serde::Serialize;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::io::{Read, Write};
use std::process::Command;
use sysinfo::{ProcessesToUpdate, System};

fn fmt_tokens(n: u64) -> String {
    if n >= 1_000_000_000 { format!("{:.1}B", n as f64 / 1e9) }
    else if n >= 1_000_000 { format!("{:.1}M", n as f64 / 1e6) }
    else if n >= 1_000 { format!("{:.1}K", n as f64 / 1e3) }
    else { n.to_string() }
}

fn label_for(name: &str, cmd: &str) -> Option<(&'static str, &'static str)> {
    let c = cmd.to_lowercase();
    let n = name.to_lowercase();
    if c.contains("claude") && !c.contains("chrome-native") {
        return Some(("Claude Code", "claude"));
    }
    let table: &[(&str, &str, &str)] = &[
        ("httpx", "HTTPX Scanner", "dev-tool"),
        ("ollama", "Ollama LLM", "dev-tool"),
        ("dolt", "Dolt DB", "dev-tool"),
        ("gitea", "Gitea", "dev-tool"),
        ("redis", "Redis", "dev-tool"),
        ("postgres", "PostgreSQL", "dev-tool"),
        ("windsurf", "Windsurf IDE", "ide"),
        ("node", "Node.js", "runtime"),
        ("python", "Python", "runtime"),
        ("docker", "Docker", "infra"),
        ("colima", "Colima VM", "infra"),
        ("lima", "Lima VM", "infra"),
    ];
    for &(key, label, cat) in table {
        if n.contains(key) || c.contains(key) {
            return Some((label, cat));
        }
    }
    None
}

#[derive(Serialize, Clone)]
struct ProcessInfo {
    pid: u32,
    label: String,
    category: String,
    cpu_percent: f32,
    mem_mb: u64,
    status: String,
    cmd: String,
}

#[derive(Serialize)]
struct Snapshot {
    timestamp: String,
    claude_active: usize,
    claude_idle: usize,
    total_cpu: f32,
    total_mem_mb: u64,
    processes: Vec<ProcessInfo>,
}

#[derive(Serialize)]
struct ServiceStatus {
    name: String,
    running: bool,
    pid: Option<u32>,
    cpu: f32,
    mem_mb: u64,
}

fn collect(sys: &System) -> Vec<ProcessInfo> {
    let mut procs: Vec<ProcessInfo> = sys
        .processes()
        .iter()
        .filter_map(|(pid, proc)| {
            let name = proc.name().to_string_lossy().to_string();
            let cmd_parts: Vec<String> = proc.cmd().iter().map(|s| s.to_string_lossy().to_string()).collect();
            let cmd_full = cmd_parts.join(" ");
            let (label, category) = label_for(&name, &cmd_full)?;
            Some(ProcessInfo {
                pid: pid.as_u32(),
                label: label.to_string(),
                category: category.to_string(),
                cpu_percent: proc.cpu_usage(),
                mem_mb: proc.memory() / (1024 * 1024),
                status: if proc.cpu_usage() > 1.0 {
                    "active".into()
                } else {
                    "idle".into()
                },
                cmd: cmd_full.chars().take(80).collect(),
            })
        })
        .collect();
    procs.sort_by(|a, b| {
        b.cpu_percent
            .partial_cmp(&a.cpu_percent)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    procs
}

fn now_iso() -> String {
    Command::new("date")
        .arg("-Iseconds")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .unwrap_or_default()
        .trim()
        .to_string()
}

const SERVICES: &[(&str, &str, &str)] = &[
    ("ollama", "ollama", "ollama serve"),
    ("docker", "Docker", "open -a Docker"),
    ("colima", "colima", "colima start"),
    ("qdrant", "qdrant", ""),
    ("gitea", "gitea", "gitea web"),
];

fn check_service(sys: &System, keyword: &str) -> ServiceStatus {
    for (pid, proc) in sys.processes() {
        let cmd: String = proc.cmd().iter().map(|s| s.to_string_lossy().to_string()).collect::<Vec<_>>().join(" ").to_lowercase();
        let name = proc.name().to_string_lossy().to_lowercase();
        if name.contains(keyword) || cmd.contains(keyword) {
            return ServiceStatus {
                name: keyword.to_string(),
                running: true,
                pid: Some(pid.as_u32()),
                cpu: proc.cpu_usage(),
                mem_mb: proc.memory() / (1024 * 1024),
            };
        }
    }
    ServiceStatus {
        name: keyword.to_string(),
        running: false,
        pid: None,
        cpu: 0.0,
        mem_mb: 0,
    }
}

fn restart_service(name: &str) -> bool {
    let cmd = match name {
        "ollama" => "killall ollama 2>/dev/null; sleep 1; nohup ollama serve &>/dev/null &",
        "docker" => "open -a Docker",
        "colima" => "colima stop 2>/dev/null; colima start",
        _ => return false,
    };
    Command::new("sh").arg("-c").arg(cmd).spawn().is_ok()
}

fn check_http_service(url: &str) -> bool {
    // Fast TCP connect check — works for Docker containers too
    use std::net::{TcpStream, ToSocketAddrs};
    use std::time::Duration;
    let host_port = url
        .trim_start_matches("http://")
        .trim_start_matches("https://")
        .split('/')
        .next()
        .unwrap_or("");
    let host_port = if host_port.contains(':') { host_port.to_string() } else { format!("{host_port}:80") };
    // Resolve hostname (handles "localhost" → 127.0.0.1)
    if let Ok(mut addrs) = host_port.to_socket_addrs() {
        if let Some(addr) = addrs.next() {
            return TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok();
        }
    }
    false
}

fn watchdog(sys: &System) -> Vec<String> {
    let mut alerts = Vec::new();
    for &(svc, keyword, _) in SERVICES {
        if svc == "colima" { continue; }
        // For qdrant/docker: check TCP port, not process name
        let running = match svc {
            "qdrant"  => check_http_service("http://localhost:6333"),
            "docker"  => {
                // Docker Desktop doesn't appear as "docker" process — check daemon socket
                let s = check_service(sys, "Docker Desktop");
                s.running || check_service(sys, "com.docker").running
                    || std::path::Path::new("/var/run/docker.sock").exists()
            }
            _ => check_service(sys, keyword).running,
        };
        if !running {
            alerts.push(format!("DOWN: {svc} is not running"));
        }
    }
    for (_, proc) in sys.processes() {
        let cmd: String = proc.cmd().iter().map(|s| s.to_string_lossy().to_string()).collect::<Vec<_>>().join(" ");
        if cmd.contains("claude") && !cmd.contains("chrome-native") && proc.cpu_usage() < 0.1 && proc.run_time() > 1800 {
            alerts.push(format!(
                "HUNG?: Claude PID {} idle {}min",
                proc.pid().as_u32(),
                proc.run_time() / 60
            ));
        }
    }
    alerts
}

#[derive(Parser)]
#[command(name = "agimon-core", about = "AGIMON Core — blazing fast agent monitor")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Full system snapshot (JSON)
    Snap,
    /// List all dev processes
    Ps,
    /// Check service health
    Health,
    /// Watchdog — check for issues
    Watch,
    /// Restart a service (ollama, docker, colima)
    Restart { service: String },
    /// Network: tunnels, listeners, external (replaces lsof Python)
    Net,
    /// Sessions — parse JSONL with simd-json (10x faster)
    Sessions { #[arg(default_value_t = 20)] limit: usize },
    /// Compact JSON for menubar IPC
    Ipc,
    /// Fast JSON for menubar (IPC + Budget + Watchdog + MLX)
    MenuData,
}

fn init_sys() -> System {
    let mut sys = System::new_all();
    sys.refresh_all();
    std::thread::sleep(std::time::Duration::from_millis(200));
    sys.refresh_processes(ProcessesToUpdate::All, true);
    sys
}

// ── Network (fast lsof parsing in Rust) ────────────────────────

#[derive(Serialize)]
struct NetConn {
    process: String,
    pid: u32,
    port: u16,
    addr: String,
    remote: String,
    state: String,
    label: String,
    kind: String, // "tunnel", "listen", "external"
}

fn port_label(port: u16) -> &'static str {
    match port {
        3000 => "Gitea", 3030 => "Grafana", 4222 => "NATS",
        5432 | 5433 | 5434 => "PostgreSQL", 6333 => "Qdrant-HTTP",
        6334 => "Qdrant-gRPC", 6379 | 6380 | 6381 => "Redis",
        7777 => "SuperJarvis", 8100 => "API", 8108 => "Typesense",
        8222 => "NATS-Mon", 9001 => "Minio", 9222 => "Chrome-Debug",
        11434 => "Ollama", 14000 => "Custom", 15432 => "PG-Tunnel",
        16379 => "Redis-Tunnel", 49998 => "Dolt",
        _ => "",
    }
}

fn parse_port(s: &str) -> (String, u16) {
    if let Some(idx) = s.rfind(':') {
        let addr = s[..idx].to_string();
        let port = s[idx + 1..].parse().unwrap_or(0);
        (addr, port)
    } else {
        (s.to_string(), 0)
    }
}

fn collect_network() -> Vec<NetConn> {
    let output = Command::new("lsof")
        .args(["-i", "-nP"])
        .output()
        .ok();
    let stdout = output
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .unwrap_or_default();

    let mut conns = Vec::new();
    let mut seen_listen = std::collections::HashSet::new();
    let mut seen_ext = std::collections::HashSet::new();

    for line in stdout.lines().skip(1) {
        let cols: Vec<&str> = line.split_whitespace().collect();
        if cols.len() < 9 { continue; }
        let process = cols[0];
        let pid: u32 = cols[1].parse().unwrap_or(0);
        let addr = cols[8];
        let state = cols.get(9).copied().unwrap_or("");

        if state.contains("LISTEN") || addr.contains("(LISTEN)") {
            let clean = addr.replace("(LISTEN)", "");
            let (a, port) = parse_port(&clean);
            let key = format!("{process}:{port}");
            if seen_listen.contains(&key) { continue; }
            seen_listen.insert(key);
            let kind = if process == "ssh" { "tunnel" } else { "listen" };
            conns.push(NetConn {
                process: process.to_string(), pid, port, addr: a,
                remote: String::new(), state: "LISTEN".into(),
                label: port_label(port).to_string(), kind: kind.into(),
            });
        } else if addr.contains("->") && state.contains("ESTABLISHED") {
            let parts: Vec<&str> = addr.splitn(2, "->").collect();
            if parts.len() < 2 { continue; }
            let remote = parts[1];
            if remote.starts_with("127.") || remote.starts_with("[::1]") { continue; }
            let key = format!("{process}:{remote}");
            if seen_ext.contains(&key) { continue; }
            seen_ext.insert(key);
            let (r_addr, r_port) = parse_port(remote);
            conns.push(NetConn {
                process: process.to_string(), pid, port: r_port,
                addr: parts[0].to_string(), remote: r_addr,
                state: "ESTABLISHED".into(),
                label: port_label(r_port).to_string(), kind: "external".into(),
            });
        }
    }
    conns
}

// ── Session Parser (simd-json, 10x faster than Python) ─────────

#[derive(Serialize)]
struct SessionInfo {
    session_id: String,
    first_message: String,
    message_count: usize,
    input_tokens: u64,
    output_tokens: u64,
    subagent_count: usize,
    tools: Vec<String>,
    timestamp: String,
    active: bool,
}

fn parse_sessions(limit: usize) -> Vec<SessionInfo> {
    let projects_dir = dirs_path().join(".claude/projects");
    if !projects_dir.exists() {
        return Vec::new();
    }

    let mut jsonl_files: Vec<(PathBuf, f64)> = Vec::new();
    if let Ok(entries) = fs::read_dir(&projects_dir) {
        for entry in entries.flatten() {
            if !entry.path().is_dir() { continue; }
            if let Ok(sub_entries) = fs::read_dir(entry.path()) {
                for sub in sub_entries.flatten() {
                    let p = sub.path();
                    if p.extension().is_some_and(|e| e == "jsonl") {
                        let mtime = sub.metadata().ok()
                            .and_then(|m| m.modified().ok())
                            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                            .map(|d| d.as_secs_f64())
                            .unwrap_or(0.0);
                        jsonl_files.push((p, mtime));
                    }
                }
            }
        }
    }
    jsonl_files.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    let cutoff = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64() - 300.0;

    let mut sessions = Vec::new();
    for (path, mtime) in jsonl_files.iter().take(limit) {
        let content = match fs::read_to_string(&path) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let sid = path.file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();

        let mut first_msg = String::new();
        let mut msg_count = 0usize;
        let mut in_tok = 0u64;
        let mut out_tok = 0u64;
        let mut tools: HashMap<String, u32> = HashMap::new();

        for line in content.lines() {
            if line.is_empty() { continue; }
            // simd-json backend → serde_json::Value API (fastest parse, familiar API)
            let mut bytes = line.as_bytes().to_vec();
            let val: serde_json::Value = match simd_json::serde::from_slice(&mut bytes) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let rec_type = val.get("type").and_then(|v| v.as_str()).unwrap_or("");

            if rec_type == "user" {
                msg_count += 1;
                if first_msg.is_empty() {
                    if let Some(content) = val.get("message").and_then(|m| m.get("content")) {
                        if let Some(s) = content.as_str() {
                            first_msg = s.chars().take(80).collect();
                        }
                    }
                }
            } else if rec_type == "assistant" {
                msg_count += 1;
                if let Some(usage) = val.get("message").and_then(|m| m.get("usage")) {
                    in_tok += usage.get("input_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
                    in_tok += usage.get("cache_read_input_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
                    out_tok += usage.get("output_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
                }
                if let Some(content) = val.get("message").and_then(|m| m.get("content")) {
                    if let Some(arr) = content.as_array() {
                        for block in arr {
                            if block.get("type").and_then(|v| v.as_str()) == Some("tool_use") {
                                let tool = block.get("name")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("unknown");
                                *tools.entry(tool.to_string()).or_insert(0) += 1;
                            }
                        }
                    }
                }
            }
        }

        // Count subagents
        let subagent_dir = path.with_extension("").join("subagents");
        let subagent_count = if subagent_dir.exists() {
            fs::read_dir(&subagent_dir)
                .map(|d| d.filter(|e| e.as_ref().is_ok_and(|e| {
                    e.path().extension().is_some_and(|ext| ext == "jsonl")
                })).count())
                .unwrap_or(0)
        } else { 0 };

        let mut sorted_tools: Vec<String> = tools.into_iter()
            .map(|(k, v)| format!("{k}({v})"))
            .collect();
        sorted_tools.sort();

        sessions.push(SessionInfo {
            session_id: sid,
            first_message: first_msg,
            message_count: msg_count,
            input_tokens: in_tok,
            output_tokens: out_tok,
            subagent_count,
            tools: sorted_tools,
            timestamp: String::new(),
            active: *mtime > cutoff,
        });
    }
    sessions
}

fn dirs_path() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/Users/master".into()))
}

fn main() {
    let cli = Cli::parse();
    let sys = init_sys();

    match cli.cmd {
        Cmd::Snap => {
            let procs = collect(&sys);
            let snap = Snapshot {
                timestamp: now_iso(),
                claude_active: procs.iter().filter(|p| p.category == "claude" && p.status == "active").count(),
                claude_idle: procs.iter().filter(|p| p.category == "claude" && p.status == "idle").count(),
                total_cpu: procs.iter().map(|p| p.cpu_percent).sum(),
                total_mem_mb: procs.iter().map(|p| p.mem_mb).sum(),
                processes: procs,
            };
            println!("{}", serde_json::to_string_pretty(&snap).unwrap_or_default());
        }
        Cmd::Ps => {
            let procs = collect(&sys);
            let mut cur_cat = String::new();
            for p in &procs {
                if p.category != cur_cat {
                    cur_cat.clone_from(&p.category);
                    let cc: f32 = procs.iter().filter(|x| x.category == cur_cat).map(|x| x.cpu_percent).sum();
                    let cm: u64 = procs.iter().filter(|x| x.category == cur_cat).map(|x| x.mem_mb).sum();
                    let cn = procs.iter().filter(|x| x.category == cur_cat).count();
                    println!("\n\x1b[1;33m{cur_cat} ({cn}) \u{2014} {cc:.1}% {cm}MB\x1b[0m");
                }
                let ic = if p.status == "active" { "\x1b[32m\u{25cf}\x1b[0m" } else { "\x1b[90m\u{25cb}\x1b[0m" };
                let w = 10;
                let f = ((p.cpu_percent / 100.0) * w as f32) as usize;
                let bar = format!("\x1b[33m{}\x1b[90m{}\x1b[0m", "\u{2588}".repeat(f.min(w)), "\u{2591}".repeat(w.saturating_sub(f)));
                println!("  {ic} {:<16} {bar} {:>5.1}%  {:>5}MB  PID:{}", p.label, p.cpu_percent, p.mem_mb, p.pid);
            }
        }
        Cmd::Health => {
            println!("\x1b[1;33m\u{1f3e5} Service Health\x1b[0m\n");
            for &(svc, keyword, _) in SERVICES {
                let s = check_service(&sys, keyword);
                let ic = if s.running { "\x1b[32m\u{25cf}\x1b[0m" } else { "\x1b[31m\u{2717}\x1b[0m" };
                let detail = if s.running {
                    format!("PID:{} CPU:{:.1}% MEM:{}MB", s.pid.unwrap_or(0), s.cpu, s.mem_mb)
                } else {
                    "NOT RUNNING".into()
                };
                println!("  {ic} {svc:<12} {detail}");
            }
        }
        Cmd::Watch => {
            let alerts = watchdog(&sys);
            if alerts.is_empty() {
                println!("\x1b[32m\u{2713} All systems healthy\x1b[0m");
            } else {
                println!("\x1b[1;31m\u{26a0} {} issues:\x1b[0m\n", alerts.len());
                for a in &alerts {
                    println!("  \x1b[31m\u{25cf}\x1b[0m {a}");
                }
            }
        }
        Cmd::Restart { service } => {
            println!("Restarting {service}...");
            if restart_service(&service) {
                println!("\x1b[32m\u{2713} Restart initiated\x1b[0m");
            } else {
                eprintln!("\x1b[31m\u{2717} Unknown service: {service}\x1b[0m");
            }
        }
        Cmd::Net => {
            let conns = collect_network();
            let tunnels: Vec<_> = conns.iter().filter(|c| c.kind == "tunnel").collect();
            let listeners: Vec<_> = conns.iter().filter(|c| c.kind == "listen").collect();
            let external: Vec<_> = conns.iter().filter(|c| c.kind == "external").collect();

            println!("\x1b[1;33m\u{1f512} SSH Tunnels ({})\x1b[0m", tunnels.len());
            for c in &tunnels {
                println!("  \x1b[36m:{:>5}\x1b[0m \u{2192} {}", c.port, if c.label.is_empty() { "unknown" } else { &c.label });
            }
            println!("\n\x1b[1;33m\u{1f4e1} Dienste ({})\x1b[0m", listeners.len());
            for c in listeners.iter().take(15) {
                println!("  {} \x1b[36m:{}\x1b[0m {}", c.process, c.port, c.label);
            }
            println!("\n\x1b[1;33m\u{1f30d} Extern ({})\x1b[0m", external.len());
            let mut proc_counts = HashMap::new();
            for c in &external {
                *proc_counts.entry(c.process.clone()).or_insert(0u32) += 1;
            }
            let mut sorted_procs: Vec<_> = proc_counts.into_iter().collect();
            sorted_procs.sort_by(|a, b| b.1.cmp(&a.1));
            for (proc, cnt) in sorted_procs.iter().take(10) {
                println!("  \x1b[33m{proc:<15}\x1b[0m {cnt}x");
            }
        }
        Cmd::Sessions { limit } => {
            let sessions = parse_sessions(limit);
            let active_count = sessions.iter().filter(|s| s.active).count();
            println!("\x1b[1;33m\u{1f4cb} Sessions ({} total, {} active)\x1b[0m\n", sessions.len(), active_count);
            for s in &sessions {
                let ic = if s.active { "\x1b[32m\u{25cf}\x1b[0m" } else { "\x1b[90m\u{25cb}\x1b[0m" };
                let ag = if s.subagent_count > 0 { format!(" {}ag", s.subagent_count) } else { String::new() };
                let tok = if s.input_tokens > 0 {
                    format!(" {}tok", fmt_tokens(s.input_tokens + s.output_tokens))
                } else { String::new() };
                let msg = if s.first_message.is_empty() { "...".to_string() } else { s.first_message.chars().take(55).collect::<String>() };
                println!("{ic} {msg}{ag}{tok}");
                if !s.tools.is_empty() {
                    println!("  \x1b[90mTools: {}\x1b[0m", s.tools.join(", "));
                }
            }
        }
        Cmd::MenuData => {
            // Fix #1: Proper double-refresh with sleep for accurate CPU measurement
            let mut sys = System::new_all();
            sys.refresh_all();
            std::thread::sleep(std::time::Duration::from_millis(300));
            sys.refresh_processes(ProcessesToUpdate::All, true);
            let procs = collect(&sys);
            let wd_alerts = watchdog(&sys);
            
            // Fix #2: Use direct TCP port check for Ollama (HTTP check was unreliable)
            let mut mlx_avail = false;
            let mut mlx_models: Vec<String> = Vec::new();
            let ollama_up = std::net::TcpStream::connect_timeout(
                &"127.0.0.1:11434".parse().unwrap(),
                std::time::Duration::from_millis(300),
            ).is_ok();
            if ollama_up {
                mlx_avail = true;
                // Fetch model list via raw HTTP
                if let Ok(mut stream) = std::net::TcpStream::connect("127.0.0.1:11434") {
                    let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(800)));
                    let req = "GET /api/tags HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n";
                    let _ = stream.write_all(req.as_bytes());
                    let mut resp = String::new();
                    let _ = stream.read_to_string(&mut resp);
                    if let Some(idx) = resp.find("\r\n\r\n") {
                        let json_str = &resp[idx+4..];
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(json_str) {
                            if let Some(arr) = v["models"].as_array() {
                                for m in arr.iter().take(5) {
                                    if let Some(n) = m["name"].as_str() {
                                        mlx_models.push(n.to_string());
                                    }
                                }
                            }
                        }
                    }
                }
            }
            
            // Fix #3: Budget - correct field name is "estimated_cost_usd", also read from stats
            let mut daily_spent = 0.0_f64;
            let mut daily_budget = 50.0_f64;
            let mut budget_alerts: Vec<String> = Vec::new();
            
            if let Ok(home) = std::env::var("HOME") {
                // Try claude's own stats first (most reliable)
                let stats_file = std::path::PathBuf::from(&home).join(".claude/stats/today-summary.json");
                if let Ok(content) = std::fs::read_to_string(&stats_file) {
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&content) {
                        if let Some(t) = v["today"].as_f64() {
                            daily_spent = t;
                        }
                    }
                }
                
                // Fallback: sum costs.jsonl with correct field name
                if daily_spent == 0.0 {
                    let cost_file = std::path::PathBuf::from(&home).join(".claude/metrics/costs.jsonl");
                    if let Ok(content) = std::fs::read_to_string(&cost_file) {
                        let today = now_iso().split('T').next().unwrap_or("").to_string();
                        for line in content.lines() {
                            if !line.contains(&today) { continue; }
                            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                                // Fix: correct field name
                                if let Some(c) = v["estimated_cost_usd"].as_f64().or(v["cost"].as_f64()) {
                                    daily_spent += c;
                                }
                            }
                        }
                    }
                }
                
                // Read budget config
                let config_file = std::path::PathBuf::from(&home).join(".claude/agimon_budget.json");
                if let Ok(content) = std::fs::read_to_string(&config_file) {
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&content) {
                        if let Some(b) = v["daily_budget_limit"].as_f64() {
                            daily_budget = b;
                        }
                    }
                }
            }
            
            if daily_spent >= daily_budget {
                budget_alerts.push(format!("⚠️ Budget überschritten: ${:.2} / ${:.0}", daily_spent, daily_budget));
            } else if daily_spent >= daily_budget * 0.8 {
                budget_alerts.push(format!("⚠️ Budget fast voll: ${:.2} / ${:.0}", daily_spent, daily_budget));
            }
            
            let json = serde_json::json!({
                "procs": procs.iter().map(|p| serde_json::json!({
                    "pid": p.pid, "label": p.label, "cat": p.category,
                    "cpu": p.cpu_percent, "mem": p.mem_mb, "s": p.status,
                })).collect::<Vec<_>>(),
                "watchdog": wd_alerts,
                "mlx": {
                    "available": mlx_avail,
                    "count": mlx_models.len(),
                    "models": mlx_models,
                },
                "budget": {
                    "spent": daily_spent,
                    "budget": daily_budget,
                    "remaining": daily_budget - daily_spent,
                    "alerts": budget_alerts,
                    "at_risk": 0
                }
            });
            println!("{}", json);
        }
        Cmd::Ipc => {
            let procs = collect(&sys);
            let compact = serde_json::json!({
                "active": procs.iter().filter(|p| p.category == "claude" && p.status == "active").count(),
                "idle": procs.iter().filter(|p| p.category == "claude" && p.status == "idle").count(),
                "total": procs.len(),
                "cpu": procs.iter().map(|p| p.cpu_percent).sum::<f32>(),
                "mem_mb": procs.iter().map(|p| p.mem_mb).sum::<u64>(),
                "procs": procs.iter().map(|p| serde_json::json!({
                    "pid": p.pid, "label": p.label, "cat": p.category,
                    "cpu": p.cpu_percent, "mem": p.mem_mb, "s": p.status,
                })).collect::<Vec<_>>(),
            });
            println!("{compact}");
        }
    }
}
