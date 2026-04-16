#!/usr/bin/env bash
# ============================================================
# wiki-backup.sh — Wiki Brain 备份脚本
#
# 将 wiki 数据目录打包为带时间戳的 tar.gz 归档。
# 路径通过环境变量或参数配置，不硬编码。
#
# 用法:
#   ./wiki-backup.sh                     # 使用默认路径
#   ./wiki-backup.sh -s /vol1/1000/wiki -d /vol1/1000/wiki-backups
#   ./wiki-backup.sh --source /path/to/wiki --dest /path/to/backups
#   ./wiki-backup.sh --dry-run           # 仅预览，不实际备份
# ============================================================

set -euo pipefail

# === 默认配置（可通过环境变量覆盖）===
WIKI_SOURCE="${WIKI_SOURCE:-}"
WIKI_BACKUP_DIR="${WIKI_BACKUP_DIR:-}"
MAX_BACKUPS="${MAX_BACKUPS:-10}"
DRY_RUN=false

# === 参数解析 ===
show_help() {
    cat <<'EOF'
Usage: wiki-backup.sh [OPTIONS]

Options:
  -s, --source DIR     Wiki 数据目录 (默认: $WIKI_SOURCE 或 ./wiki)
  -d, --dest DIR       备份目标目录 (默认: $WIKI_BACKUP_DIR 或 ./backups)
  -k, --keep N         保留最近 N 份备份 (默认: 10)
  -n, --dry-run        仅预览，不实际执行
  -h, --help           显示此帮助信息

Environment variables:
  WIKI_SOURCE       Wiki 数据目录
  WIKI_BACKUP_DIR   备份目标目录
  MAX_BACKUPS       最大备份数量
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--source)
            WIKI_SOURCE="$2"
            shift 2
            ;;
        -d|--dest)
            WIKI_BACKUP_DIR="$2"
            shift 2
            ;;
        -k|--keep)
            MAX_BACKUPS="$2"
            shift 2
            ;;
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            show_help
            exit 1
            ;;
    esac
done

# === 路径解析（环境变量 > 参数 > 默认值）===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "$WIKI_SOURCE" ]]; then
    # 尝试从 wiki_config.py 推断，或使用脚本目录的上级
    if [[ -d "${SCRIPT_DIR}/../wiki" ]]; then
        WIKI_SOURCE="$(cd "${SCRIPT_DIR}/../wiki" && pwd)"
    else
        WIKI_SOURCE="${SCRIPT_DIR}"
    fi
fi

if [[ -z "$WIKI_BACKUP_DIR" ]]; then
    WIKI_BACKUP_DIR="${WIKI_SOURCE}-backups"
fi

# === 验证 ===
if [[ ! -d "$WIKI_SOURCE" ]]; then
    echo "错误: Wiki 目录不存在: $WIKI_SOURCE" >&2
    exit 1
fi

# === 执行备份 ===
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${WIKI_BACKUP_DIR}/wiki_${TIMESTAMP}.tar.gz"

# 排除的目录和文件
EXCLUDES=(
    --exclude='__pycache__'
    --exclude='.git'
    --exclude='*.pyc'
    --exclude='.DS_Store'
    --exclude='node_modules'
    --exclude='*.tmp'
    --exclude='.env'
    --exclude='.env.*'
)

echo "=== Wiki Backup ==="
echo "源目录:   $WIKI_SOURCE"
echo "目标文件: $BACKUP_FILE"
echo "保留数量: $MAX_BACKUPS"

if [[ "$DRY_RUN" == true ]]; then
    echo "[DRY RUN] 将创建: $BACKUP_FILE"
    echo "[DRY RUN] tar czf ${EXCLUDES[*]} ..."
    # 列出将要打包的文件数量
    FILE_COUNT=$(find "$WIKI_SOURCE" -name '*.md' -not -path '*__pycache__*' | wc -l)
    echo "[DRY RUN] 将打包 ${FILE_COUNT} 个 markdown 文件"
else
    mkdir -p "$WIKI_BACKUP_DIR"
    tar czf "$BACKUP_FILE" "${EXCLUDES[@]}" -C "$(dirname "$WIKI_SOURCE")" "$(basename "$WIKI_SOURCE")"
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "✓ 备份完成: $BACKUP_FILE ($BACKUP_SIZE)"
fi

# === 清理旧备份 ===
if [[ "$DRY_RUN" == true ]]; then
    EXCESS=$(ls -1t "${WIKI_BACKUP_DIR}"/wiki_*.tar.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | wc -l)
    if [[ "$EXCESS" -gt 0 ]]; then
        echo "[DRY RUN] 将删除 ${EXCESS} 份旧备份"
    fi
else
    ls -1t "${WIKI_BACKUP_DIR}"/wiki_*.tar.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | while read -r old; do
        echo "✓ 删除旧备份: $(basename "$old")"
        rm -f "$old"
    done
fi

echo "=== 完成 ==="
