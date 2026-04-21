"""Qdrant integration — uses official qdrant-client (gRPC when available)."""
from __future__ import annotations
import hashlib
from datetime import datetime

QDRANT_URL = "http://localhost:6333"
COLLECTION = "agimon"
VECTOR_DIM = 384

_client = None
_encoder = None


def _get_client():
    global _client
    if _client is None:
        try:
            from qdrant_client import QdrantClient
            _client = QdrantClient(url=QDRANT_URL, timeout=5)
        except ImportError:
            return None
    return _client


def _get_encoder():
    global _encoder
    if _encoder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _encoder = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            return None
    return _encoder


def _ensure_collection() -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.get_collection(COLLECTION)
        return True
    except Exception:
        pass
    try:
        from qdrant_client.models import VectorParams, Distance
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        return True
    except Exception:
        return False


def index_session(session_id: str, text: str, metadata: dict) -> bool:
    """Index a session into Qdrant."""
    client = _get_client()
    encoder = _get_encoder()
    if client is None or encoder is None:
        return False
    if not _ensure_collection():
        return False

    try:
        from qdrant_client.models import PointStruct
        vector = encoder.encode(text).tolist()
        point_id = int(hashlib.md5(session_id.encode()).hexdigest()[:8], 16)
        client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "session_id": session_id,
                    "text": text[:2000],
                    "timestamp": datetime.now().isoformat(),
                    **metadata,
                },
            )],
        )
        return True
    except Exception:
        return False


def search_sessions(query: str, limit: int = 10) -> list[dict]:
    """Semantic search over indexed sessions."""
    client = _get_client()
    encoder = _get_encoder()
    if client is None or encoder is None:
        return []
    if not _ensure_collection():
        return []

    try:
        vector = encoder.encode(query).tolist()
        results = client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=limit,
            with_payload=True,
        )
        return [
            {**hit.payload, "score": hit.score}
            for hit in results.points
        ]
    except Exception:
        return []


def get_collection_stats() -> dict:
    """Get Qdrant collection stats."""
    client = _get_client()
    if client is None:
        return {"points": 0, "vectors": 0, "status": "offline"}
    try:
        info = client.get_collection(COLLECTION)
        return {
            "points": info.points_count or 0,
            "vectors": info.vectors_count or 0,
            "status": str(info.status),
        }
    except Exception:
        return {"points": 0, "vectors": 0, "status": "offline"}
