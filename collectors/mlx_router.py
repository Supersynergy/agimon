"""MLX/Local Model Router for AGIMON.

Intelligently routes tasks to local MLX models when appropriate to save costs.
"""
from __future__ import annotations
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Task complexity classification
TASK_COMPLEXITY = {
    "simple": {
        "keywords": [
            "list", "find", "grep", "search", "count", "format",
            "rename", "move", "copy", "delete", "read", "show",
            "what is", "what are", "simple", "basic", "check if"
        ],
        "max_tokens": 500,
        "mlx_suitable": True,
    },
    "moderate": {
        "keywords": [
            "refactor", "modify", "update", "add feature", "implement",
            "fix bug", "debug", "analyze", "review", "compare",
            "explain", "summarize", "document"
        ],
        "max_tokens": 2000,
        "mlx_suitable": True,
    },
    "complex": {
        "keywords": [
            "architect", "design", "redesign", "restructure",
            "complex", "difficult", "challenging", "advanced",
            "security audit", "performance", "optimization",
            "multi-file", "cross-module"
        ],
        "max_tokens": 5000,
        "mlx_suitable": False,
    }
}

# MLX model capabilities
MLX_MODELS = {
    "llama3": {
        "context": 8192,
        "good_for": ["summarization", "simple coding", "qa"],
        "avoid": ["complex reasoning", "multi-step tasks"],
    },
    "gemma3n": {
        "context": 128000,
        "good_for": ["long context", "coding", "analysis"],
        "avoid": [],
    },
    "phi4": {
        "context": 16000,
        "good_for": ["coding", "reasoning"],
        "avoid": ["very long contexts"],
    },
    "qwen3": {
        "context": 128000,
        "good_for": ["multilingual", "coding", "long context"],
        "avoid": [],
    },
    "mistral-small": {
        "context": 32000,
        "good_for": ["general tasks", "coding"],
        "avoid": ["complex math"],
    },
}


@dataclass
class RoutingDecision:
    use_local: bool
    model: str
    reason: str
    estimated_savings: float
    confidence: float


def check_ollama_available() -> bool:
    """Check if Ollama is running and accessible."""
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return len(data.get("models", [])) > 0
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return False


def get_available_local_models() -> list[str]:
    """Get list of available Ollama models."""
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return [m.get("name", m.get("model", "")) for m in data.get("models", [])]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return []


def classify_task_complexity(task: str) -> str:
    """Classify task as simple, moderate, or complex."""
    task_lower = task.lower()
    
    # Check for complex indicators first (most specific)
    for keyword in TASK_COMPLEXITY["complex"]["keywords"]:
        if keyword in task_lower:
            return "complex"
    
    # Check for simple indicators
    simple_score = sum(1 for k in TASK_COMPLEXITY["simple"]["keywords"] if k in task_lower)
    if simple_score >= 1:
        return "simple"
    
    # Check for moderate
    moderate_score = sum(1 for k in TASK_COMPLEXITY["moderate"]["keywords"] if k in task_lower)
    if moderate_score >= 1:
        return "moderate"
    
    # Default to moderate for unknown tasks
    return "moderate"


def select_mlx_model(task: str, complexity: str) -> Optional[str]:
    """Select the best MLX model for a task."""
    available = get_available_local_models()
    if not available:
        return None
    
    # Map available models to our known models
    model_mapping = {
        "llama3": "llama3",
        "gemma3n": "gemma3n",
        "phi4": "phi4",
        "qwen3": "qwen3",
        "mistral-small": "mistral-small",
    }
    
    # Find best matching model
    for avail in available:
        for key, mapped in model_mapping.items():
            if key in avail.lower():
                return mapped
    
    # Fallback to first available
    return available[0].split(":")[0] if available else None


def should_route_to_local(task: str, context_length: int = 0) -> RoutingDecision:
    """Decide whether to route a task to a local MLX model."""
    
    # Check if Ollama is available
    if not check_ollama_available():
        return RoutingDecision(
            use_local=False,
            model="claude-sonnet",
            reason="Ollama not available",
            estimated_savings=0.0,
            confidence=1.0,
        )
    
    # Classify complexity
    complexity = classify_task_complexity(task)
    
    # Complex tasks always go to Claude
    if complexity == "complex":
        return RoutingDecision(
            use_local=False,
            model="claude-opus",
            reason=f"Task classified as complex - requires advanced reasoning",
            estimated_savings=0.0,
            confidence=0.9,
        )
    
    # Check context length
    if context_length > 8000:
        # For long contexts, prefer models with large context windows
        model = select_mlx_model(task, complexity)
        if model and MLX_MODELS.get(model, {}).get("context", 0) < context_length:
            return RoutingDecision(
                use_local=False,
                model="claude-sonnet",
                reason=f"Context length ({context_length}) exceeds MLX model capacity",
                estimated_savings=0.0,
                confidence=0.8,
            )
    
    # Simple and moderate tasks can use MLX
    if complexity in ("simple", "moderate"):
        model = select_mlx_model(task, complexity)
        if model:
            # Estimate savings
            estimated_input = len(task.split()) * 2  # rough token estimate
            if complexity == "simple":
                claude_cost = (estimated_input / 1000) * 0.015  # sonnet pricing
            else:
                claude_cost = (estimated_input / 1000) * 0.015
            
            return RoutingDecision(
                use_local=True,
                model=f"ollama/{model}",
                reason=f"Task classified as {complexity} - MLX can handle efficiently",
                estimated_savings=round(claude_cost, 4),
                confidence=0.75 if complexity == "simple" else 0.6,
            )
    
    # Default: use Claude
    return RoutingDecision(
        use_local=False,
        model="claude-sonnet",
        reason="Unable to confidently classify - defaulting to Claude",
        estimated_savings=0.0,
        confidence=0.5,
    )


def query_local_model(model: str, prompt: str, timeout: int = 60) -> Optional[str]:
    """Query a local Ollama model."""
    try:
        result = subprocess.run(
            [
                "curl", "-s", "http://localhost:11434/api/generate",
                "-d", json.dumps({
                    "model": model,
                    "prompt": prompt[:4000],  # Truncate for safety
                    "stream": False,
                })
            ],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("response", "")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None


def get_routing_report() -> str:
    """Generate a report of routing decisions for recent sessions."""
    from collectors.sessions import load_recent_sessions, get_active_session_ids
    
    sessions = load_recent_sessions(limit=10)
    active_ids = get_active_session_ids()
    
    lines = [
        "🚀 MLX Routing Report",
        "",
        f"Ollama Available: {'✅ Yes' if check_ollama_available() else '❌ No'}",
        f"Local Models: {', '.join(get_available_local_models()[:5]) or 'None'}",
        "",
        "Recent Sessions Routing Analysis:",
    ]
    
    total_savings = 0.0
    for session in sessions:
        if session.session_id in active_ids and session.first_user_message:
            decision = should_route_to_local(
                session.first_user_message,
                session.input_tokens + session.output_tokens
            )
            
            if decision.use_local:
                total_savings += decision.estimated_savings
                lines.append(f"  ✅ LOCAL: {session.session_id[:12]}...")
                lines.append(f"     Model: {decision.model}")
                lines.append(f"     Savings: ${decision.estimated_savings:.4f}")
            else:
                lines.append(f"  ☁️  CLOUD: {session.session_id[:12]}...")
                lines.append(f"     Model: {decision.model}")
                lines.append(f"     Reason: {decision.reason}")
            lines.append("")
    
    lines.append(f"💰 Estimated Total Savings: ${total_savings:.2f}")
    lines.append("")
    lines.append("Routing Rules:")
    lines.append("  • Simple tasks (find, grep, list) → MLX")
    lines.append("  • Moderate tasks (refactor, explain) → MLX if confident")
    lines.append("  • Complex tasks (architect, audit) → Claude Opus/Sonnet")
    lines.append("  • Long context (>8K tokens) → Check MLX capacity")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(get_routing_report())
