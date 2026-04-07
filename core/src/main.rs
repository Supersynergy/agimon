//! AGIMON Core — blazing fast process monitor + service health + watchdog
//! Replaces Python subprocess calls with native sysinfo (100x faster)

use clap::{Parser, Subcommand};
use serde::Serialize;
use std::collections::HashMap;
use std::process::Command;
use sysinfo::{ProcessesToUpdate, System};

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

fn watchdog(sys: &System) -> Vec<String> {
    let mut alerts = Vec::new();
    for &(svc, keyword, _) in SERVICES {
        let s = check_service(sys, keyword);
        if !s.running {
            alerts.push(format!("DOWN: {svc} is not running"));
        } else if s.cpu > 90.0 {
            alerts.push(format!("HIGH CPU: {svc} at {:.1}%", s.cpu));
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
    /// Compact JSON for menubar IPC
    Ipc,
}

fn init_sys() -> System {
    let mut sys = System::new_all();
    sys.refresh_all();
    std::thread::sleep(std::time::Duration::from_millis(200));
    sys.refresh_processes(ProcessesToUpdate::All, true);
    sys
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
        Cmd::Ipc => {
            let procs = collect(&sys);
            let compact = serde_json::json!({
                "active": procs.iter().filter(|p| p.category == "claude" && p.status == "active").count(),
                "idle": procs.iter().filter(|p| p.category == "claude" && p.status == "idle").count(),
                "total": procs.len(),
                "cpu": procs.iter().map(|p| p.cpu_percent).sum::<f32>(),
                "mem_mb": procs.iter().map(|p| p.mem_mb).sum::<u64>(),
                "procs": procs.iter().take(20).map(|p| serde_json::json!({
                    "pid": p.pid, "label": p.label, "cat": p.category,
                    "cpu": p.cpu_percent, "mem": p.mem_mb, "s": p.status,
                })).collect::<Vec<_>>(),
            });
            println!("{compact}");
        }
    }
}
