"""Cost Prediction & Budget Control for AGIMON.

Predicts session costs before they escalate and provides auto-pause functionality.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from collectors.sessions import load_recent_sessions, get_active_session_ids, Session

BUDGET_FILE = Path.home() / ".claude" / "agimon_budget.json"
ALERT_THRESHOLDS = {
    "session_cost_warning": 5.0,  # $5 per session
    "session_cost_critical": 10.0,  # $10 per session
    "daily_budget_warning": 50.0,  # $50 per day
    "daily_budget_critical": 100.0,  # $100 per day
}

# Model pricing (per 1K tokens)
MODEL_PRICING = {
    "claude-opus-4-6": {"input": 0.075, "output": 0.075, "cache_read": 0.0075, "cache_write": 0.01875},
    "claude-sonnet-4-6": {"input": 0.015, "output": 0.015, "cache_read": 0.0015, "cache_write": 0.00375},
    "claude-haiku-4-5": {"input": 0.001, "output": 0.001, "cache_read": 0.0001, "cache_write": 0.00025},
    "local": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
}


@dataclass
class CostPrediction:
    session_id: str
    current_cost: float
    predicted_cost: float
    confidence: float  # 0-1 based on data quality
    trend: str  # "stable", "rising", "spiking"
    recommendation: str
    time_to_budget: Optional[str]  # "30min", "2h", etc.


@dataclass
class BudgetStatus:
    daily_spent: float
    daily_budget: float
    daily_remaining: float
    projected_daily: float
    sessions_at_risk: list[str]
    alerts: list[str]


def estimate_session_cost(session: Session) -> float:
    """Estimate cost from session tokens and tools used."""
    # Default to sonnet pricing if unknown
    pricing = MODEL_PRICING.get("claude-sonnet-4-6")
    
    # Try to detect model from tools/session context
    tools_str = str(session.tools_used)
    if "opus" in tools_str.lower() or session.input_tokens > 500000:
        pricing = MODEL_PRICING.get("claude-opus-4-6")
    elif "haiku" in tools_str.lower() or session.input_tokens < 10000:
        pricing = MODEL_PRICING.get("claude-haiku-4-5")
    
    input_cost = (session.input_tokens / 1000) * pricing["input"]
    output_cost = (session.output_tokens / 1000) * pricing["output"]
    return round(input_cost + output_cost, 2)


def predict_session_trajectory(session: Session) -> CostPrediction:
    """Predict where a session's cost is heading."""
    current_cost = estimate_session_cost(session)
    
    # Simple trend analysis based on message rate and tool usage
    msg_count = session.message_count
    tool_count = sum(session.tools_used.values())
    subagent_count = len(session.subagents)
    
    # Higher tool/subagent usage = higher burn rate
    intensity_score = min(1.0, (tool_count + subagent_count * 2) / max(msg_count, 1))
    
    # Prediction factors
    if intensity_score > 0.7:
        multiplier = 3.0  # Spiking
        trend = "spiking"
    elif intensity_score > 0.4:
        multiplier = 1.5  # Rising
        trend = "rising"
    else:
        multiplier = 1.1  # Stable
        trend = "stable"
    
    predicted = current_cost * multiplier
    confidence = 0.5 + (intensity_score * 0.4)  # 0.5-0.9 based on data
    
    # Generate recommendation
    if predicted > ALERT_THRESHOLDS["session_cost_critical"]:
        recommendation = "STOP: Session will exceed $10. Consider killing or switching to Haiku."
    elif predicted > ALERT_THRESHOLDS["session_cost_warning"]:
        recommendation = "WARNING: Session trending toward $5. Monitor closely."
    elif intensity_score > 0.6 and msg_count > 50:
        recommendation = "TIP: High activity detected. Consider delegating subtasks to Haiku."
    else:
        recommendation = "OK: Session within normal parameters."
    
    # Time to budget (rough estimate)
    if current_cost > 0 and trend == "spiking":
        remaining = ALERT_THRESHOLDS["session_cost_warning"] - current_cost
        rate = current_cost / max(msg_count, 1)  # cost per message
        if rate > 0:
            msgs_to_budget = remaining / rate
            if msgs_to_budget < 20:
                time_to_budget = "<5min"
            elif msgs_to_budget < 100:
                time_to_budget = "~30min"
            else:
                time_to_budget = f"~{int(msgs_to_budget / 10)}h"
        else:
            time_to_budget = None
    else:
        time_to_budget = None
    
    return CostPrediction(
        session_id=session.session_id,
        current_cost=current_cost,
        predicted_cost=round(predicted, 2),
        confidence=round(confidence, 2),
        trend=trend,
        recommendation=recommendation,
        time_to_budget=time_to_budget,
    )


def load_budget_config() -> dict:
    """Load user's budget configuration."""
    if BUDGET_FILE.exists():
        try:
            return json.loads(BUDGET_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "daily_budget": 50.0,
        "session_limit": 10.0,
        "auto_pause_enabled": False,
        "alerts_enabled": True,
    }


def save_budget_config(config: dict) -> None:
    """Save budget configuration."""
    BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_FILE.write_text(json.dumps(config, indent=2))


def get_budget_status() -> BudgetStatus:
    """Get current budget status and alerts."""
    config = load_budget_config()
    daily_budget = config.get("daily_budget", 50.0)
    
    # Calculate today's spend
    today = datetime.now().strftime("%Y-%m-%d")
    sessions = load_recent_sessions(limit=50)
    
    daily_spent = 0.0
    sessions_at_risk = []
    predictions = []
    
    for session in sessions:
        pred = predict_session_trajectory(session)
        # Rough date check - assume recent sessions are today for simplicity
        daily_spent += pred.current_cost
        predictions.append(pred)
        
        if pred.trend in ("rising", "spiking") and pred.predicted_cost > ALERT_THRESHOLDS["session_cost_warning"]:
            sessions_at_risk.append(session.session_id[:12])
    
    daily_remaining = daily_budget - daily_spent
    
    # Project end-of-day spend based on current trajectory
    active_sessions = [p for p in predictions if p.trend != "stable"]
    if active_sessions:
        projected_additional = sum(p.predicted_cost - p.current_cost for p in active_sessions)
        projected_daily = daily_spent + projected_additional
    else:
        projected_daily = daily_spent
    
    # Generate alerts
    alerts = []
    if daily_remaining < 0:
        alerts.append(f"CRITICAL: Daily budget exceeded by ${abs(daily_remaining):.2f}!")
    elif daily_remaining < daily_budget * 0.1:
        alerts.append(f"WARNING: Only ${daily_remaining:.2f} remaining today.")
    
    if len(sessions_at_risk) > 2:
        alerts.append(f"Multiple sessions ({len(sessions_at_risk)}) trending toward cost limits.")
    
    return BudgetStatus(
        daily_spent=round(daily_spent, 2),
        daily_budget=daily_budget,
        daily_remaining=round(daily_remaining, 2),
        projected_daily=round(projected_daily, 2),
        sessions_at_risk=sessions_at_risk[:5],
        alerts=alerts,
    )


def should_auto_pause(session_id: str) -> tuple[bool, str]:
    """Determine if a session should be auto-paused."""
    config = load_budget_config()
    if not config.get("auto_pause_enabled", False):
        return False, "Auto-pause disabled"
    
    sessions = load_recent_sessions(limit=20)
    for session in sessions:
        if session.session_id.startswith(session_id):
            pred = predict_session_trajectory(session)
            
            if pred.predicted_cost > config.get("session_limit", 10.0):
                return True, f"Predicted cost ${pred.predicted_cost} exceeds limit"
            
            if pred.trend == "spiking" and pred.time_to_budget == "<5min":
                return True, "Cost spiking - will exceed budget in <5min"
            
            # Check if daily budget nearly exhausted
            budget = get_budget_status()
            if budget.daily_remaining < 5.0 and pred.trend != "stable":
                return True, f"Daily budget nearly exhausted (${budget.daily_remaining} left)"
            
            return False, "Within budget parameters"
    
    return False, "Session not found"


def format_cost_report() -> str:
    """Generate a formatted cost prediction report."""
    budget = get_budget_status()
    sessions = load_recent_sessions(limit=10)
    active_ids = get_active_session_ids()
    
    lines = [
        "💰 Cost Prediction Report",
        f"",
        f"Daily Budget:    ${budget.daily_budget:.2f}",
        f"Spent Today:     ${budget.daily_spent:.2f}",
        f"Remaining:       ${budget.daily_remaining:.2f}",
        f"Projected EOD:   ${budget.projected_daily:.2f}",
        f"",
    ]
    
    if budget.alerts:
        lines.append("⚠️  Alerts:")
        for alert in budget.alerts:
            lines.append(f"   • {alert}")
        lines.append("")
    
    lines.append("Active Sessions:")
    for session in sessions:
        if session.session_id in active_ids:
            pred = predict_session_trajectory(session)
            status_emoji = {"stable": "✅", "rising": "⚠️", "spiking": "🚨"}.get(pred.trend, "❓")
            lines.append(f"  {status_emoji} {session.session_id[:12]}...")
            lines.append(f"     Current: ${pred.current_cost:.2f} → Predicted: ${pred.predicted_cost:.2f}")
            lines.append(f"     Trend: {pred.trend} | {pred.recommendation}")
            if pred.time_to_budget:
                lines.append(f"     ⏱️  To budget limit: {pred.time_to_budget}")
            lines.append("")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_cost_report())
