#!/bin/bash
# wiki-backup.sh — Wiki Brain 数据备份脚本
# 用法: ./wiki-backup.sh [--keep N]
# 默认保留最近 7 个备份

KEEP=7
BACKUP_DIR="/vol1/1000/wiki-backups"
WIKI_DIR="/vol1/1000/wiki"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="wiki_${DATE}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

for arg in "$@"; do
    case $arg in
        --keep) shift; KEEP=${1:-7}; shift ;;
    esac
done

mkdir -p "${BACKUP_DIR}"

echo "=== Wiki Backup: ${DATE} ==="

echo "[1/3] Creating archive..."
tar czf "${BACKUP_PATH}.tar.gz" \
    --exclude='logs' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='agency-agents' \
    -C "${WIKI_DIR}" \
    registry.json \
    SCHEMA.md \
    RESOLVER.md \
    index.md \
    log.md \
    concepts/ \
    entities/ \
    people/ \
    projects/ \
    meetings/ \
    ideas/ \
    graph.json \
    src/ \
    .env \
    docker-compose.yml \
    Dockerfile \
    scripts/ \
    2>&1

TAR_SIZE=$(du -sh "${BACKUP_PATH}.tar.gz" | cut -f1)
echo "  Archive: ${BACKUP_NAME}.tar.gz (${TAR_SIZE})"

echo "[2/3] Verifying archive..."
if tar tzf "${BACKUP_PATH}.tar.gz" >/dev/null 2>&1; then
    echo "  OK: archive is valid"
else
    echo "  FAIL: archive is corrupted!"
    rm -f "${BACKUP_PATH}.tar.gz"
    exit 1
fi

echo "[3/3] Cleaning up (keeping ${KEEP} most recent)..."
ls -t "${BACKUP_DIR}"/wiki_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs rm -f --
REMAINING=$(ls "${BACKUP_DIR}"/wiki_*.tar.gz 2>/dev/null | wc -l)
echo "  Remaining backups: ${REMAINING}"

echo "=== Backup complete ==="
