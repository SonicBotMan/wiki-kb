#!/usr/bin/env python3
"""
Vector indexer for wiki pages using bge-small-zh-v1.5 embedding.
Scans /data/ directory for .md files and builds a vector index.
"""

import os
import json
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path

# Configuration
WIKI_DIR = os.environ.get("WIKI_ROOT", "/data")
VECTOR_INDEX_FILE = os.path.join(WIKI_DIR, "vector_index.json")
EMBEDDING_API = os.environ.get("EMBEDDING_API", "http://openviking:11435/v1/embeddings")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
BATCH_SIZE = 32
EMBEDDING_DIM = 384

_EXCLUDE_DIRS = {
    "_EXCLUDE_DIRS", "node_modules", ".git", "__pycache__", 
    ".obsidian", ".trash", "temp", "tmp"
}

_FRONTMATTER_KEYS = {"title", "type", "executive_summary", "key_facts"}


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}, content
    
    parts = content[3:].split("---", 1)
    if len(parts) < 2:
        return {}, content
    
    frontmatter_text = parts[0].strip()
    body = parts[1].strip()
    
    frontmatter = {}
    for line in frontmatter_text.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    
    return frontmatter, body


def extract_section(content: str, section_name: str) -> str:
    """Extract a section from markdown content by heading."""
    lines = content.split("\n")
    section_lines = []
    in_section = False
    
    for line in lines:
        if line.startswith("## ") and section_name.lower() in line.lower():
            in_section = True
            continue
        elif line.startswith("## "):
            in_section = False
        elif in_section:
            section_lines.append(line)
    
    return "\n".join(section_lines).strip()


def get_page_text(page_path: Path) -> str:
    """Extract text from a wiki page for embedding."""
    try:
        with open(page_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {page_path}: {e}")
        return ""
    
    frontmatter, body = parse_frontmatter(content)
    
    title = frontmatter.get("title", page_path.stem)
    executive_summary = frontmatter.get("executive_summary", "")
    key_facts = frontmatter.get("key_facts", "")
    
    if not executive_summary:
        executive_summary = extract_section(body, "executive summary")
    if not key_facts:
        key_facts = extract_section(body, "key facts")
    
    # Text template as specified
    text = f"{title}\n{executive_summary}\n{key_facts}"
    return text


def get_embedding(texts: list[str]) -> list[list[float]]:
    """Get embeddings from bge-small-zh-v1.5 API (OpenAI-compatible format)."""
    payload = {"input": texts, "model": EMBEDDING_MODEL}
    
    try:
        req = urllib.request.Request(
            EMBEDDING_API,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
            # OpenAI format: {"data": [{"embedding": [...], "index": 0}]}
            data = result.get("data", [])
            if not data:
                return []
            # Sort by index to maintain order
            data.sort(key=lambda x: x.get("index", 0))
            return [item["embedding"] for item in data]
    except urllib.error.URLError as e:
        print(f"Embedding API error: {e}")
        return []


def scan_wiki_pages() -> list[Path]:
    """Scan wiki directory for .md files."""
    pages = []
    wiki_path = Path(WIKI_DIR)
    
    for root, dirs, files in os.walk(wiki_path):
        # Filter out excluded directories
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        
        for file in files:
            if file.endswith(".md"):
                pages.append(Path(root) / file)
    
    return pages


def get_file_mtime(page_path: Path) -> float:
    """Get file modification time."""
    try:
        return os.path.getmtime(page_path)
    except:
        return 0


def load_existing_index() -> dict:
    """Load existing vector index."""
    if os.path.exists(VECTOR_INDEX_FILE):
        try:
            with open(VECTOR_INDEX_FILE, "r", encoding="utf-8") as f:
                return {item["page_path"]: item for item in json.load(f)}
        except:
            return {}
    return {}


def save_index(index_data: list[dict]):
    """Save vector index to file."""
    with open(VECTOR_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)


def build_index(force: bool = False):
    """Build vector index for all wiki pages."""
    print(f"Scanning wiki pages in {WIKI_DIR}...")
    pages = scan_wiki_pages()
    print(f"Found {len(pages)} wiki pages")
    
    if not pages:
        print("No pages found. Exiting.")
        return
    
    existing_index = {} if force else load_existing_index()
    index_data = []
    to_embed = []
    
    # Check which pages need re-indexing
    for page_path in pages:
        page_path_str = str(page_path)
        mtime = get_file_mtime(page_path)
        
        if not force and page_path_str in existing_index:
            existing = existing_index[page_path_str]
            if existing.get("mtime", 0) >= mtime:
                # Skip if mtime hasn't changed
                index_data.append(existing)
                continue
        
        # Get text for embedding
        text = get_page_text(page_path)
        if text.strip():
            frontmatter, _ = parse_frontmatter(open(page_path, "r", encoding="utf-8").read())
            to_embed.append({
                "page_path": page_path_str,
                "title": frontmatter.get("title", page_path.stem),
                "type": frontmatter.get("type", "page"),
                "text": text,
                "mtime": mtime
            })
    
    print(f"Pages to embed: {len(to_embed)} (force={force})")
    
    # Batch embedding
    for i in range(0, len(to_embed), BATCH_SIZE):
        batch = to_embed[i:i + BATCH_SIZE]
        texts = [item["text"] for item in batch]
        
        print(f"Embedding batch {i // BATCH_SIZE + 1}/{(len(to_embed) + BATCH_SIZE - 1) // BATCH_SIZE}...")
        
        embeddings = get_embedding(texts)
        
        if not embeddings:
            print(f"Warning: Failed to get embeddings for batch starting at {i}")
            continue
        
        for j, item in enumerate(batch):
            if j < len(embeddings):
                item["embedding"] = embeddings[j]
                index_data.append(item)
        
        # Small delay to avoid overwhelming the API
        if i + BATCH_SIZE < len(to_embed):
            time.sleep(0.5)
    
    # Merge with existing items that weren't reprocessed
    for page_path_str, existing_item in existing_index.items():
        if existing_item not in index_data:
            index_data.append(existing_item)
    
    save_index(index_data)
    print(f"Vector index saved to {VECTOR_INDEX_FILE}")
    print(f"Total indexed pages: {len(index_data)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build vector index for wiki pages")
    parser.add_argument("--force", action="store_true", help="Force rebuild of entire index")
    args = parser.parse_args()
    
    build_index(force=args.force)
