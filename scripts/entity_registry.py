#!/usr/bin/env python3
"""
Entity Registry Core Module
管理实体的唯一ID、别名解析、去重检测、wiki页面扫描自动注册
"""

import json
import re
import os
import sys
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

# === 路径配置 ===
REGISTRY_PATH = Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki"))) / "registry.json"

# === 导入 wiki_utils ===
_wiki_utils_available = False
try:
    sys.path.insert(0, str(Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki"))) / "scripts"))
    import wiki_utils
    _wiki_utils_available = True
except ImportError:
    pass


# === 数据层 ===

def load_registry() -> dict:
    """加载 registry.json，不存在返回空结构"""
    if not REGISTRY_PATH.exists():
        return _empty_registry()
    
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return _empty_registry()


def save_registry(reg: dict) -> None:
    """保存到 registry.json（原子写入：先写.tmp再rename）"""
    reg["version"] = 1
    
    # 确保目录存在
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    tmp_path = REGISTRY_PATH.with_suffix(".tmp")
    
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    
    # 原子替换
    tmp_path.rename(REGISTRY_PATH)


def _empty_registry() -> dict:
    """返回空registry结构"""
    now = datetime.now().isoformat()
    return {
        "version": 1,
        "entities": {},
        "alias_index": {},
        "page_index": {},
        "stats": {
            "total_entities": 0,
            "total_aliases": 0,
            "total_pages": 0,
            "last_scan": now
        }
    }


# === 工具函数 ===

def generate_id() -> str:
    """生成 ent_ 前缀的短UUID（如 ent_a1b2c3）"""
    short_id = uuid.uuid4().hex[:6]
    return f"ent_{short_id}"


def normalize_name(name: str) -> str:
    """统一化：lowercase, strip, 去掉多余空格"""
    if not name:
        return ""
    # 转小写，去首尾空格，多个空格合并为一个
    name = name.lower().strip()
    name = re.sub(r'\s+', ' ', name)
    return name


def _get_frontmatter(content: str):
    """
    提取markdown文件的frontmatter。
    优先使用 wiki_utils，如果不可用则使用内置实现。
    """
    if _wiki_utils_available:
        return wiki_utils.get_frontmatter(content)
    
    # 内置实现
    match = re.match(r'^---\n(.*?)\n---\n(.*)$', content, re.DOTALL)
    if match:
        fm_text = match.group(1)
        body = match.group(2)
        fm = {}
        for line in fm_text.split('\n'):
            m = re.match(r'^(\w+):\s*(.*)$', line)
            if m:
                key, val = m.group(1), m.group(2)
                # 解析简单值
                val = val.strip().strip("'\"")
                fm[key] = val
        return fm, body
    return {}, content


def _parse_tags(tag_str: str) -> list:
    """解析tags字符串为列表"""
    if not tag_str:
        return []
    # 移除 [] 并分割
    tag_str = tag_str.strip('[]')
    if not tag_str:
        return []
    tags = [t.strip() for t in tag_str.split(',')]
    return [t for t in tags if t]


# === 别名生成 ===

def _remove_brackets(text: str) -> str:
    """去掉括号内容（中文括号和英文括号）"""
    # 去掉中文括号及其内容
    text = re.sub(r'【[^】]*】', '', text)
    text = re.sub(r'［[^］]*］', '', text)
    text = re.sub(r'（[^）]*）', '', text)
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    return text.strip()


def _extract_core_words(text: str) -> list:
    """提取核心词（去除常见助词、停用词，保留名词/动词）"""
    # 常见中文停用词
    stop_words = {
        '的', '是', '在', '了', '和', '与', '或', '及', '等', '把', '被',
        '的', '地', '得', '着', '过', '来', '去', '上', '下', '中', '内',
        '外', '前', '后', '里', '间', '为', '对', '这', '那', '有', '无',
        '也', '都', '而', '且', '之', '以', '于', '从', '到', '通过',
        '正在', '成为', '一个', '一件', '一篇', '一个', '可以', '能够'
    }
    
    words = []
    # 按中英文混合分词
    # 先把英文词和中文词分开处理
    parts = re.split(r'([a-zA-Z0-9]+)', text)
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.match(r'^[a-zA-Z0-9]+$', part):
            # 英文词，直接保留
            words.append(part.lower())
        else:
            # 中文词，简单按长度提取
            # 去除标点
            clean = re.sub(r'[^\w]', '', part)
            if len(clean) >= 2:
                words.append(clean)
    
    # 过滤停用词和太短的词
    result = [w for w in words if w not in stop_words and len(w) >= 2]
    return result


def _generate_aliases(title: str, page_path: str = "") -> list[str]:
    """从title生成多个别名变体"""
    aliases = []
    
    # 1. 原始title normalize
    aliases.append(normalize_name(title))
    
    # 2. 去掉括号内容
    no_bracket = _remove_brackets(title)
    if no_bracket and no_bracket != title:
        aliases.append(normalize_name(no_bracket))
    
    # 3. 英文小写形式
    english_lower = title.lower()
    if english_lower != normalize_name(title):
        aliases.append(english_lower)
    
    # 4. 文件名 stem
    if page_path:
        stem = Path(page_path).stem
        if stem:
            aliases.append(stem.lower())
            aliases.append(normalize_name(stem))
    
    # 5. 核心关键词
    core_words = _extract_core_words(title)
    for word in core_words:
        if word and word != normalize_name(title):
            aliases.append(word)
    
    # 6. 去除重复，保留唯一
    seen = set()
    unique_aliases = []
    for a in aliases:
        a = a.strip()
        if a and a not in seen:
            seen.add(a)
            unique_aliases.append(a)
    
    return unique_aliases


# === 核心操作 ===

def resolve(name: str) -> Optional[dict]:
    """通过任意名称（canonical_name 或 alias）查找实体"""
    reg = load_registry()
    norm = normalize_name(name)
    
    # 先查 alias_index
    entity_id = reg["alias_index"].get(norm)
    if entity_id and entity_id in reg["entities"]:
        return reg["entities"][entity_id]
    
    # 再遍历 entities 的 canonical_name
    for entity in reg["entities"].values():
        if normalize_name(entity.get("canonical_name", "")) == norm:
            return entity
    
    return None


def register(name: str, entity_type: str, page_path: str = "", aliases: list = None, reg: dict = None) -> dict:
    """注册新实体。如果name或alias已存在，返回已有实体"""
    # 如果没有传入 registry，则加载
    should_save = reg is None
    if reg is None:
        reg = load_registry()
    
    norm = normalize_name(name)
    
    # 1. 精确检查 alias_index
    if norm in reg["alias_index"]:
        entity_id = reg["alias_index"][norm]
        return reg["entities"][entity_id]
    
    # 2. 检查 canonical_name 是否已存在（精确匹配）
    for entity in reg["entities"].values():
        if normalize_name(entity.get("canonical_name", "")) == norm:
            return entity
    
    # 3. 模糊匹配：去掉括号后匹配
    no_bracket_norm = normalize_name(_remove_brackets(name))
    if no_bracket_norm and no_bracket_norm != norm:
        if no_bracket_norm in reg["alias_index"]:
            entity_id = reg["alias_index"][no_bracket_norm]
            return reg["entities"][entity_id]
        for entity in reg["entities"].values():
            if normalize_name(_remove_brackets(entity.get("canonical_name", ""))) == no_bracket_norm:
                return entity
    
    # 4. 包含关系匹配
    for entity in reg["entities"].values():
        can_name = entity.get("canonical_name", "")
        can_norm = normalize_name(can_name)
        # 检查 name 是否包含于已有实体，或已有实体包含于 name
        if can_norm and (norm in can_norm or can_norm in norm):
            return entity
    
    # 5. 创建新实体
    entity_id = generate_id()
    now = datetime.now().strftime("%Y-%m-%d")
    
    new_entity = {
        "id": entity_id,
        "canonical_name": name.strip(),
        "aliases": [],
        "type": entity_type,
        "primary_page": page_path,
        "related_pages": [],
        "created": now,
        "updated": now,
        "metadata": {}
    }
    
    # 生成别名
    generated_aliases = _generate_aliases(name, page_path)
    if aliases:
        for a in aliases:
            norm_a = normalize_name(a)
            if norm_a and norm_a not in generated_aliases:
                generated_aliases.append(norm_a)
    
    # 添加到 registry
    reg["entities"][entity_id] = new_entity
    
    # 更新索引
    for alias in generated_aliases:
        # 检查是否被其他实体占用
        if alias in reg["alias_index"]:
            print(f"  [WARNING] Alias '{alias}' already used by another entity, skipping")
            continue
        reg["alias_index"][alias] = entity_id
        new_entity["aliases"].append(alias)
    
    if page_path:
        reg["page_index"][page_path] = entity_id
    
    # 更新统计
    reg["stats"]["total_entities"] = len(reg["entities"])
    reg["stats"]["total_aliases"] = len(reg["alias_index"])
    reg["stats"]["total_pages"] = len(reg["page_index"])
    
    if should_save:
        save_registry(reg)
    
    return new_entity


def merge(entity_id: str, into_id: str) -> bool:
    """将 entity_id 合并到 into_id。返回是否成功"""
    if entity_id == into_id:
        return False
    
    reg = load_registry()
    
    if entity_id not in reg["entities"] or into_id not in reg["entities"]:
        return False
    
    source = reg["entities"][entity_id]
    target = reg["entities"][into_id]
    
    # 合并 aliases（避免重复）
    for alias in source.get("aliases", []):
        if alias not in target["aliases"]:
            target["aliases"].append(alias)
            reg["alias_index"][alias] = into_id
    
    # 合并 related_pages（避免重复）
    for page in source.get("related_pages", []):
        if page not in target["related_pages"]:
            target["related_pages"].append(page)
            reg["page_index"][page] = into_id
    
    # 如果 source 的 primary_page 不在 target 的 pages 中
    if source.get("primary_page") and source["primary_page"] not in target["related_pages"]:
        target["related_pages"].append(source["primary_page"])
        reg["page_index"][source["primary_page"]] = into_id
    
    # 更新 target 的时间
    target["updated"] = datetime.now().strftime("%Y-%m-%d")
    
    # 删除源实体
    for alias in source.get("aliases", []):
        if reg["alias_index"].get(alias) == entity_id:
            del reg["alias_index"][alias]
    
    for page in source.get("related_pages", []):
        if reg["page_index"].get(page) == entity_id:
            del reg["page_index"][page]
    
    if reg["page_index"].get(source.get("primary_page", "")) == entity_id:
        del reg["page_index"][source["primary_page"]]
    
    del reg["entities"][entity_id]
    
    # 更新统计
    reg["stats"]["total_entities"] = len(reg["entities"])
    reg["stats"]["total_aliases"] = len(reg["alias_index"])
    reg["stats"]["total_pages"] = len(reg["page_index"])
    
    save_registry(reg)
    return True


def update_entity(entity_id: str, **kwargs) -> bool:
    """更新实体字段（canonical_name, type, aliases, primary_page 等）"""
    reg = load_registry()
    
    if entity_id not in reg["entities"]:
        return False
    
    entity = reg["entities"][entity_id]
    
    # 需要重建索引的字段
    rebuild_alias = False
    old_aliases = set(entity.get("aliases", []))
    
    for key, value in kwargs.items():
        if key == "canonical_name":
            entity["canonical_name"] = value
        elif key == "type":
            entity["type"] = value
        elif key == "aliases":
            entity["aliases"] = value
            rebuild_alias = True
        elif key == "primary_page":
            # 从旧page_index移除
            if entity.get("primary_page"):
                old_page = entity["primary_page"]
                if reg["page_index"].get(old_page) == entity_id:
                    del reg["page_index"][old_page]
            entity["primary_page"] = value
            if value:
                reg["page_index"][value] = entity_id
        elif key == "related_pages":
            entity["related_pages"] = value
        elif key == "metadata":
            entity["metadata"] = value
    
    entity["updated"] = datetime.now().strftime("%Y-%m-%d")
    
    # 重建 alias_index
    if rebuild_alias:
        for alias in old_aliases:
            if reg["alias_index"].get(alias) == entity_id:
                del reg["alias_index"][alias]
        
        for alias in entity.get("aliases", []):
            norm = normalize_name(alias)
            if norm not in reg["alias_index"]:
                reg["alias_index"][norm] = entity_id
            entity["aliases"] = entity.get("aliases", [])
    
    reg["stats"]["total_aliases"] = len(reg["alias_index"])
    reg["stats"]["total_pages"] = len(reg["page_index"])
    
    save_registry(reg)
    return True


def add_alias(entity_id: str, alias: str) -> bool:
    """给实体添加别名。如果alias已被其他实体占用，返回False"""
    reg = load_registry()
    
    if entity_id not in reg["entities"]:
        return False
    
    norm = normalize_name(alias)
    if not norm:
        return False
    
    # 检查是否被占用
    if norm in reg["alias_index"] and reg["alias_index"][norm] != entity_id:
        return False
    
    entity = reg["entities"][entity_id]
    
    if norm not in entity.get("aliases", []):
        entity.setdefault("aliases", []).append(alias)
    
    reg["alias_index"][norm] = entity_id
    entity["updated"] = datetime.now().strftime("%Y-%m-%d")
    
    reg["stats"]["total_aliases"] = len(reg["alias_index"])
    
    save_registry(reg)
    return True


def remove_alias(alias: str) -> bool:
    """移除别名"""
    reg = load_registry()
    norm = normalize_name(alias)
    
    if norm not in reg["alias_index"]:
        return False
    
    entity_id = reg["alias_index"][norm]
    
    if entity_id in reg["entities"]:
        entity = reg["entities"][entity_id]
        entity["aliases"] = [a for a in entity.get("aliases", []) if normalize_name(a) != norm]
        entity["updated"] = datetime.now().strftime("%Y-%m-%d")
    
    del reg["alias_index"][norm]
    reg["stats"]["total_aliases"] = len(reg["alias_index"])
    
    save_registry(reg)
    return True


def get_all_entities() -> list:
    """返回所有实体列表"""
    reg = load_registry()
    return list(reg["entities"].values())


def get_by_type(entity_type: str) -> list:
    """按类型过滤实体"""
    reg = load_registry()
    return [e for e in reg["entities"].values() if e.get("type") == entity_type]


def find_duplicates() -> list:
    """发现可能重复的实体对（基于名称相似度）"""
    reg = load_registry()
    entities = list(reg["entities"].values())
    duplicates = []
    
    for i, e1 in enumerate(entities):
        name1 = normalize_name(e1.get("canonical_name", ""))
        aliases1 = set(e1.get("aliases", []))
        
        for e2 in entities[i+1:]:
            name2 = normalize_name(e2.get("canonical_name", ""))
            aliases2 = set(e2.get("aliases", []))
            
            # 检查是否名字相似
            score = 0
            
            # 1. 名字完全匹配
            if name1 == name2:
                score = 1.0
            # 2. 包含关系
            elif name1 in name2 or name2 in name1:
                score = 0.8
            # 3. 去掉括号后匹配
            elif _remove_brackets(name1) == _remove_brackets(name2):
                score = 0.9
            # 4. 别名重叠
            elif aliases1 & aliases2:
                score = 0.7
            # 5. 核心词相同
            else:
                words1 = set(_extract_core_words(name1))
                words2 = set(_extract_core_words(name2))
                if words1 and words2 and words1 == words2:
                    score = 0.6
            
            if score >= 0.6:
                duplicates.append((e1["id"], e2["id"], score))
    
    return duplicates


# === Wiki 扫描 ===

def scan_wiki_pages(wiki_root: str = None) -> dict:
    """扫描所有 wiki 页面的 frontmatter，自动注册/更新实体"""
    if wiki_root is None:
        wiki_root = str(Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki"))))
    
    wiki_path = Path(wiki_root).expanduser()
    
    if not wiki_path.exists():
        print(f"Wiki root not found: {wiki_path}")
        return {"registered": 0, "updated": 0, "skipped": 0}
    
    stats = {"registered": 0, "updated": 0, "skipped": 0}
    reg = load_registry()
    
    # 收集所有 md 文件
    md_files = list(wiki_path.rglob("*.md"))
    
    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
            fm, body = _get_frontmatter(content)
            
            if not fm:
                stats["skipped"] += 1
                continue
            
            title = fm.get("title", "")
            entity_type = fm.get("type", "entity")
            
            if not title:
                stats["skipped"] += 1
                continue
            
            # 计算相对路径
            rel_path = str(md_file.relative_to(wiki_path))
            
            # 尝试解析 tags
            tags_str = fm.get("tags", "")
            tags = _parse_tags(tags_str)
            
            # 查找是否已存在
            existing_entity = None
            
            # 1. 通过 page_index 查找
            if rel_path in reg["page_index"]:
                entity_id = reg["page_index"][rel_path]
                existing_entity = reg["entities"].get(entity_id)
            
            # 2. 通过 title/alias 查找
            if not existing_entity:
                existing_entity = resolve(title)
            
            if existing_entity:
                # 更新现有实体
                needs_update = False
                
                if existing_entity.get("canonical_name") != title:
                    # 检查新title是否冲突
                    if not resolve(title) or resolve(title)["id"] == existing_entity["id"]:
                        existing_entity["canonical_name"] = title
                        needs_update = True
                
                # 更新 primary_page
                if existing_entity.get("primary_page") != rel_path:
                    # 移除旧page索引
                    old_page = existing_entity.get("primary_page", "")
                    if old_page and reg["page_index"].get(old_page) == existing_entity["id"]:
                        del reg["page_index"][old_page]
                    existing_entity["primary_page"] = rel_path
                    reg["page_index"][rel_path] = existing_entity["id"]
                    needs_update = True
                
                # 更新 type
                if existing_entity.get("type") != entity_type:
                    existing_entity["type"] = entity_type
                    needs_update = True
                
                # 更新 metadata
                if tags:
                    existing_entity["metadata"]["tags"] = tags
                    needs_update = True
                
                if needs_update:
                    existing_entity["updated"] = datetime.now().strftime("%Y-%m-%d")
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                # 注册新实体
                new_entity = register(
                    name=title,
                    entity_type=entity_type,
                    page_path=rel_path,
                    aliases=tags,
                    reg=reg  # 传入当前registry，避免重复加载/保存
                )
                
                if new_entity:
                    # 设置 metadata
                    if tags:
                        new_entity["metadata"]["tags"] = tags
                    
                    # 更新 related_pages 中的旧条目
                    # 查找是否有其他页面关联到同名实体
                    for other_path, other_id in list(reg["page_index"].items()):
                        if other_id == new_entity["id"] and other_path != rel_path:
                            if other_path not in new_entity.get("related_pages", []):
                                new_entity.setdefault("related_pages", []).append(other_path)
                    
                    stats["registered"] += 1
                else:
                    stats["skipped"] += 1
        
        except Exception as e:
            print(f"Error processing {md_file}: {e}")
            stats["skipped"] += 1
    
    # 更新统计和时间
    reg["stats"]["total_entities"] = len(reg["entities"])
    reg["stats"]["total_aliases"] = len(reg["alias_index"])
    reg["stats"]["total_pages"] = len(reg["page_index"])
    reg["stats"]["last_scan"] = datetime.now().isoformat()
    
    save_registry(reg)
    
    return stats


def rebuild_indexes(reg: dict) -> dict:
    """从 entities 列表重建 alias_index 和 page_index"""
    reg["alias_index"] = {}
    reg["page_index"] = {}
    
    for entity in reg["entities"].values():
        entity_id = entity["id"]
        
        # 重建 page_index
        if entity.get("primary_page"):
            reg["page_index"][entity["primary_page"]] = entity_id
        
        for page in entity.get("related_pages", []):
            if page and page != entity.get("primary_page"):
                reg["page_index"][page] = entity_id
        
        # 重建 alias_index
        for alias in entity.get("aliases", []):
            norm = normalize_name(alias)
            if norm:
                # 冲突处理：保留先出现的
                if norm not in reg["alias_index"]:
                    reg["alias_index"][norm] = entity_id
    
    reg["stats"]["total_entities"] = len(reg["entities"])
    reg["stats"]["total_aliases"] = len(reg["alias_index"])
    reg["stats"]["total_pages"] = len(reg["page_index"])
    
    return reg


# === CLI ===

def main():
    """CLI 入口"""
    import sys
    
    args = sys.argv[1:]
    
    if not args or args[0] == "scan":
        # 扫描 wiki 页面注册实体
        print("Scanning wiki pages...")
        stats = scan_wiki_pages()
        print(f"  Registered: {stats['registered']}")
        print(f"  Updated: {stats['updated']}")
        print(f"  Skipped: {stats['skipped']}")
    
    elif args[0] == "list":
        # 列出所有实体
        entities = get_all_entities()
        if not entities:
            print("No entities found.")
            return
        
        print(f"Total entities: {len(entities)}\n")
        for e in entities:
            print(f"  [{e['id']}] {e['canonical_name']} ({e.get('type', 'unknown')})")
            if e.get("aliases"):
                print(f"      Aliases: {', '.join(e['aliases'][:5])}")
            if e.get("primary_page"):
                print(f"      Page: {e['primary_page']}")
    
    elif args[0] == "search":
        # 搜索实体
        if len(args) < 2:
            print("Usage: entity_registry.py search NAME")
            return
        
        name = " ".join(args[1:])
        entity = resolve(name)
        
        if entity:
            print(f"Found: [{entity['id']}] {entity['canonical_name']}")
            print(f"  Type: {entity.get('type', 'unknown')}")
            print(f"  Aliases: {', '.join(entity.get('aliases', []))}")
            print(f"  Primary Page: {entity.get('primary_page', 'N/A')}")
            print(f"  Related Pages: {', '.join(entity.get('related_pages', []))}")
        else:
            print(f"Entity not found: {name}")
    
    elif args[0] == "dedup":
        # 检测重复
        duplicates = find_duplicates()
        if not duplicates:
            print("No duplicates found.")
            return
        
        print(f"Found {len(duplicates)} potential duplicate pairs:\n")
        reg = load_registry()
        for id1, id2, score in duplicates:
            e1 = reg["entities"].get(id1, {})
            e2 = reg["entities"].get(id2, {})
            print(f"  [{score:.2f}] {e1.get('canonical_name', id1)} <-> {e2.get('canonical_name', id2)}")
            print(f"      IDs: {id1} <-> {id2}")
    
    elif args[0] == "merge" and len(args) >= 3:
        # 合并两个实体
        id1, id2 = args[1], args[2]
        print(f"Merging {id1} into {id2}...")
        success = merge(id1, id2)
        if success:
            print("Merge successful.")
        else:
            print("Merge failed. Check IDs.")
    
    elif args[0] == "rebuild":
        # 重建索引
        print("Rebuilding indexes...")
        reg = load_registry()
        reg = rebuild_indexes(reg)
        save_registry(reg)
        print(f"Done. Entities: {reg['stats']['total_entities']}, Aliases: {reg['stats']['total_aliases']}, Pages: {reg['stats']['total_pages']}")
    
    elif args[0] == "stats":
        # 显示统计
        reg = load_registry()
        stats = reg["stats"]
        print("Registry Statistics:")
        print(f"  Total Entities: {stats['total_entities']}")
        print(f"  Total Aliases: {stats['total_aliases']}")
        print(f"  Total Pages: {stats['total_pages']}")
        print(f"  Last Scan: {stats.get('last_scan', 'Never')}")
    
    else:
        print("""Entity Registry CLI

Usage:
  python3 entity_registry.py scan       # 扫描 wiki 页面注册实体
  python3 entity_registry.py list        # 列出所有实体
  python3 entity_registry.py search NAME # 搜索实体
  python3 entity_registry.py dedup       # 检测重复
  python3 entity_registry.py merge ID1 ID2  # 合并两个实体
  python3 entity_registry.py rebuild    # 重建索引
  python3 entity_registry.py stats       # 显示统计信息
""")


if __name__ == "__main__":
    main()
