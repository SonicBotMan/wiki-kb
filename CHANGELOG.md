# Changelog

All notable changes to the wiki-kb project will be documented in this file.

## [1.0.0] - 2026-04-15

### Added
- **wiki-to-notion**: 9 种类型映射（entity/concept/tool/person/project/meeting/idea/comparison/query），支持 `--delete` 自动归档
- **wiki-to-notion**: `DBS_WITH_TYPE_PROP` 白名单，防止对无 Type 属性的数据库设置 Type（400 错误）
- **wiki-to-notion**: 重复条目自动检测与归档（按 wiki_file 分组，保留最新条目）
- **wiki-to-notion**: Notion archived blocks 过滤，删除时 400 错误静默跳过
- **wiki_mcp_server**: `_EXCLUDE_DIRS` + `_is_excluded()` 排除非 wiki 内容目录
- **wiki_mcp_server**: wiki_create 事务性写入（entity_registry 注册失败时 unlink 回滚）
- **entity_registry**: `_parse_tags()` 支持 YAML list 输入（`tags: [a, b]`）
- **dream_cycle**: `get_frontmatter` tuple wrapper（正确解包 `(fm, body)` 为 `fm` dict）

### Changed
- **wiki-to-notion**: 按目录名映射 Notion DB（原 frontmatter type 映射），frontmatter type 作为 Notion Type select 属性
- **wiki-to-notion**: 删除 WebDAV 同步代码（~100 行死代码）和未使用的 sync_state 缓存
- **entity_registry**: fcntl.flock() 排他锁（30s 超时），防止并发写入损坏 registry.json
- **wiki_mcp_server**: SIGTERM/SIGINT graceful shutdown handler

### Fixed
- **dream_cycle**: `from wiki_utils import get_frontmatter as parse_frontmatter` 返回 tuple，调用处 `.get()` 报 AttributeError
- **entity_registry**: `_parse_tags()` 收到 YAML list 输入时 `.strip()` 报 AttributeError
- **wiki_mcp_server**: `_logger.addHandler(_sh)` 在 if 块外，handlers 已存在时 `_sh` 未定义导致 NameError
- **wiki_mcp_server**: `_resolve_page_path` glob 返回未走 `_validate_path()` 校验
- **wiki_mcp_server**: `wiki_create` frontmatter `yaml.dump()` 缺少 `sort_keys=False`
- **wiki_mcp_server**: `wiki_append_timeline` 新条目插入到已有条目前面（非 append）
- **wiki_mcp_server**: `wiki_search` 参数名 `type` 遗漏未改为 `entity_type`
- **wiki_mcp_server**: `_atomic_write` 的 `os.fsync()` 在 `with` 块外（fd 已关闭）

### Security
- **wiki_mcp_server**: Path Traversal 防护 — `_validate_path()` + type 白名单 + content 1MB 限制
- **wiki_mcp_server**: `_validate_type()` 白名单 9 种合法类型
