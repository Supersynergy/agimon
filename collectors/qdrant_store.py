"""Qdrant integration for searchable session history."""
from __future__ import annotations
import hashlib
import json
import time
from datetime import datetime

QDRANT_URL = "http://localhost:6333"
COLLECTION = "claude_monitor"
VECTOR_DIM = 384  # all-MiniLM-L6-v2

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _encoder = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            return None
    return _encoder


def _ensure_collection():
    """Create collection if it doesn't exist."""
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"{QDRANT_URL}/collections/{COLLECTION}")
        if resp.status == 200:
            return True
    except Exception:
        pass
    try:
        data = json.dumps({
            "vectors": {"size": VECTOR_DIM, "distance": "Cosine"}
        }).encode()
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{COLLECTION}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        urllib.request.urlopen(req)
        return True
    except Exception:
        return False


def index_session(session_id: str, text: str, metadata: dict) -> bool:
    """Index a session into Qdrant for semantic search."""
    import urllib.request
    encoder = _get_encoder()
    if encoder is None:
        return False
    if not _ensure_collection():
        return False

    vector = encoder.encode(text).tolist()
    point_id = int(hashlib.md5(session_id.encode()).hexdigest()[:8], 16)

    payload = {
        "session_id": session_id,
        "text": text[:2000],
        "timestamp": datetime.utcnow().isoformat(),
        **metadata,
    }

    data = json.dumps({
        "points": [{"id": point_id, "vector": vector, "payload": payload}]
    }).encode()

    try:
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{COLLECTION}/points",
            data=data,
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        urllib.request.urlopen(req)
        return True
    except Exception:
        return False


def search_sessions(query: str, limit: int = 10) -> list[dict]:
    """Semantic search over indexed sessions."""
    import urllib.request
    encoder = _get_encoder()
    if encoder is None:
        return []
    if not _ensure_collection():
        return []

    vector = encoder.encode(query).tolist()
    data = json.dumps({
        "vector": vector,
        "limit": limit,
        "with_payload": True,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read())
        return [
            {**hit["payload"], "score": hit["score"]}
            for hit in result.get("result", [])
        ]
    except Exception:
        return []


def get_collection_stats() -> dict:
    """Get Qdrant collection statistics."""
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"{QDRANT_URL}/collections/{COLLECTION}")
        data = json.loads(resp.read())
        info = data.get("result", {})
        return {
            "points": info.get("points_count", 0),
            "vectors": info.get("vectors_count", 0),
            "status": info.get("status", "unknown"),
        }
    except Exception:
        return {"points": 0, "vectors": 0, "status": "offline"}
