"""LLM helpers: local Ollama + MiniMax M2.7 API."""
from __future__ import annotations
import json
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434"
MINIMAX_URL = "https://api.minimax.io/v1/text/chatcompletion_v2"


def minimax_key() -> str | None:
    try:
        d = json.loads(Path("~/.gg/auth.json").expanduser().read_text())
        return (d.get("minimax") or {}).get("accessToken")
    except Exception:
        return None


def ollama_quick(prompt: str, model: str = "gemma3:270m", timeout: int = 6) -> str:
    try:
        r = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception:
        return ""


def minimax_chat(prompt: str, system: str = "", timeout: int = 15) -> str:
    key = minimax_key()
    if not key:
        return ""
    body = {
        "model": "MiniMax-M2.7-highspeed",
        "messages": ([{"role": "system", "content": system}] if system else []) +
                    [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "stream": False,
    }
    req = urllib.request.Request(
        MINIMAX_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def run_async(fn, on_done, *args, **kwargs):
    """Fire fn(*args,**kwargs) in a daemon thread, call on_done(result) when ready."""
    def _wrap():
        try:
            r = fn(*args, **kwargs)
        except Exception:
            r = ""
        try:
            on_done(r)
        except Exception:
            pass
    threading.Thread(target=_wrap, daemon=True).start()
