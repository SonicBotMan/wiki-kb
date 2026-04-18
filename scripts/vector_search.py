#!/usr/bin/env python3
"""
Vector search for wiki pages using bge-small-zh-v1.5 embedding.
Performs cosine similarity search on the vector index.
"""

import json
import math
import os
import urllib.request
from pathlib import Path
from typing import Optional

# Configuration
VECTOR_INDEX_FILE = os.path.join(os.environ.get("WIKI_ROOT", "/data"), "vector_index.json")
EMBEDDING_API = os.environ.get("EMBEDDING_API", "http://openviking:11435/v1/embeddings")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")


def load_index() -> list[dict]:
    """Load vector index from file."""
    if not Path(VECTOR_INDEX_FILE).exists():
        return []
    
    try:
        with open(VECTOR_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading index: {e}")
        return []


def get_query_embedding(text: str) -> Optional[list[float]]:
    """Get embedding for query text from bge-small-zh-v1.5 API (OpenAI-compatible format)."""
    payload = {"input": text, "model": EMBEDDING_MODEL}
    
    try:
        req = urllib.request.Request(
            EMBEDDING_API,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
            # OpenAI format: {"data": [{"embedding": [...], "index": 0}]}
            data = result.get("data", [])
            if data:
                return data[0]["embedding"]
            return None
    except urllib.error.URLError as e:
        print(f"Embedding API error: {e}")
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two vectors (pure Python, no numpy)."""
    if len(a) != len(b):
        return 0.0
    
    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)


def vector_search(query: str, top_k: int = 5) -> list[dict]:
    """
    Search wiki pages by vector similarity.
    
    Args:
        query: Search query text
        top_k: Number of results to return
        
    Returns:
        List of search results matching the output format of _fallback_file_search:
        [{title, type, page_path, summary}]
    """
    index_data = load_index()
    
    if not index_data:
        return []
    
    # Get query embedding
    query_embedding = get_query_embedding(query)
    
    if not query_embedding:
        return []
    
    # Calculate similarities and sort
    results = []
    for item in index_data:
        if "embedding" not in item:
            continue
        
        similarity = cosine_similarity(query_embedding, item["embedding"])
        results.append({
            "title": item.get("title", ""),
            "type": item.get("type", "page"),
            "page_path": item.get("page_path", ""),
            "summary": item.get("text", "")[:200] if item.get("text") else "",
            "score": similarity
        })
    
    # Sort by similarity (descending) and take top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    
    # Return in expected format (without score field)
    return [
        {
            "title": r["title"],
            "type": r["type"],
            "page_path": r["page_path"],
            "summary": r["summary"]
        }
        for r in results[:top_k]
    ]


def search(query: str, top_k: int = 5) -> list[dict]:
    """Convenience function for searching - alias for vector_search."""
    return vector_search(query, top_k)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python vector_search.py <query> [top_k]")
        sys.exit(1)
    
    query = sys.argv[1]
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    
    results = vector_search(query, top_k)
    
    print(f"\nSearch results for: '{query}'")
    print("=" * 60)
    
    for i, result in enumerate(results, 1):
        print(f"\n{i}. {result['title']} ({result['type']})")
        print(f"   Path: {result['page_path']}")
        print(f"   Summary: {result['summary'][:100]}...")
