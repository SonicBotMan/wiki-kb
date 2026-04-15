# Changelog

All notable changes to the wiki-kb project will be documented in this file.

## [Unreleased]

### Added
- **wiki-kb-sync.sh**: 一键同步脚本，支持 `--check`（漂移检测）、`--files`（文件列表）、`--changelog`（生成日志）模式，内置敏感信息扫描和 README 双语对齐检查

## [1.1.1] - 2026-04-15

Post-review bugfixes — 3 bugs found by expert code review, all fixed and verified (27/27 tests pass).

### Fixed
- **BUG-1**: Test suite `conftest.py` now properly mocks `mcp` package — test environment no longer requires FastMCP installed. 27/27 pass (was 7 pass / 20 error).
- **BUG-2**: `wiki_stats()` no longer crashes when `entity_registry` is unavailable — added `_REGISTRY_AVAILABLE` guard with fallback to empty stats.
- **BUG-3**: `wiki_append_timeline()` race condition fixed — `read_text()` moved inside `_FileLock` scope. Previously read was outside lock, allowing concurrent appends to lose data.
- Removed last Chinese error message in `entity_merge()` → `"Merge failed: one or both entities do not exist"`.
- `wiki-kb-sync.sh`: `GIT_NAME` uses `${VAR:?msg}` pattern instead of hardcoded default.

## [1.1.0] - 2026-04-15

Security hardening and code quality overhaul (RFC wiki-kb-issue-v2). 15 issues fixed across 3 batches, 27 tests added.

### Security (Batch 1 — `058181c`)
- **P0-1**: Atomic writes via `_safe_write()` — `tempfile.NamedTemporaryFile` + `os.fsync()` + `os.replace()`. Replaced all 3 bare `write_text()` calls in `wiki_create`, `wiki_update`, `wiki_append_timeline`.
- **P0-2**: Path traversal fix — `_validate_path()` now guards both exact-match and `rglob` fuzzy-match branches in `_resolve_page_path()`.
- **P0-3**: Timeline section protection — `wiki_update()` rejects `timeline`/`Timeline`/` timeline ` (case+whitespace-insensitive). Section whitelist enforced: only `Executive Summary`, `Key Facts`, `Relations` allowed.
- **P3-1**: `_FileLock` (fcntl.flock) protects all write operations — prevents CLI↔Gateway concurrent write corruption. Per-page lock files, not global.
- **P3-3**: Startup warning when `MCP_API_KEY` is not set (server runs without authentication).

### Refactor (Batch 2 — `24889cc`)
- **P1-1**: Unified frontmatter parser — `wiki_mcp_server` now imports `get_frontmatter` from `wiki_utils` (removed duplicate 15-line regex-based parser). Date normalization: `datetime.date` objects auto-converted to `YYYY-MM-DD` strings.
- **P1-4**: Replaced 3 silent `except Exception: continue` with `_logger.warning()` + context.
- **P1-5**: Removed 82-line inline `entity_registry` stub. Clean `from entity_registry import EntityRegistry` + `_REGISTRY_AVAILABLE` flag. All 4 entity tools check availability before use. `wiki_create` degrades gracefully when registry unavailable.
- **P2-1**: Test framework — 5 test files, 27 tests covering path validation, atomic writes, file locks, timeline protection, section whitelist, frontmatter parsing + date normalization.
- **P2-4**: Error/log messages converted from Chinese to English (12 messages across path validation, type checking, page lookup, startup/shutdown).

### Refactor (Batch 3 — `7fcbac1`)
- **P1-2**: Unified `TYPE_DIR_MAP`, `ALLOWED_SUBDIRS`, `ALLOWED_TYPES` to `wiki_utils.py`. Eliminated 3 hardcoded directory lists in `wiki_mcp_server.py`. `_resolve_page_path()` uses `sorted(ALLOWED_SUBDIRS)`, `wiki_create()` uses shared `TYPE_DIR_MAP`.
- **P1-6**: Enhanced `wiki_health()` — 6 checks (registry integrity, entity_registry availability, disk writable, disk space, OpenViking connectivity, page count). All messages in English.
- **P3-2**: `wiki_create` rollback — if `entity_registry.register()` fails after page creation, page is deleted and `RuntimeError` raised. Prevents orphan pages.
- **P2-4**: Final Chinese message cleanup — `entity_merge` template + `wiki_create` template converted to English.

### Fixed
- **Batch 2**: `_logger.addHandler(_sh)` was outside `if not _logger.handlers:` block — caused `NameError` when handlers already existed.
- **Batch 3**: Restored `if __name__ == "__main__"` entry point (lost during batch-1 function deletion). Fixed `mcp.run()` call to use `transport="streamable-http"` (FastMCP API).
- **Batch 3**: Removed duplicate `@mcp.tool()` decorators that caused "Tool already exists" warnings on startup. Cleared stale `__pycache__` on deploy.

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
