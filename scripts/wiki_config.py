#!/usr/bin/env python3
"""
Wiki Brain 集中配置模块。
所有脚本共享此配置，避免散落在各文件中。

环境变量优先级：环境变量 > wiki_config.yaml > 默认值
"""

import os
import yaml
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# === Wiki 目录结构 ===
_WIKI_SUBDIRS = [
    "concepts", "entities", "people",
]


@dataclass
class WikiConfig:
    """集中配置。"""
    # 路径
    wiki_root: Path
    registry_file: Path
    schema_file: Path
    env_file: Path

    # 子目录 (只读属性)
    subdirs: dict = field(default_factory=dict, repr=False)

    # MCP Server
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8764

    # OpenViking
    openviking_endpoint: str = "http://localhost:1933"
    openviking_api_key: str = ""
    openviking_account: str = "hermes"
    openviking_user: str = "default"
    openviking_bin: str = ""  # CLI binary path (optional)

    # LLM config removed in v4 (dream_cycle/memory_to_wiki deleted)

    # Notion (for wiki-to-notion)
    notion_api_key: str = ""
    webdav_base_url: str = ""
    webdav_user: str = ""
    webdav_pass: str = ""

    def __post_init__(self):
        """构建子目录路径映射。"""
        for subdir in _WIKI_SUBDIRS:
            self.subdirs[subdir] = self.wiki_root / subdir

    def subdir(self, name: str) -> Path:
        """获取子目录路径。"""
        return self.subdirs.get(name, self.wiki_root / name)


def _load_yaml_config(config_path: Path) -> dict:
    """加载 wiki_config.yaml（如果存在）。"""
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                if cfg and isinstance(cfg, dict):
                    logger.debug("加载配置文件: %s", config_path)
                    return cfg
        except Exception as e:
            logger.warning("配置文件加载失败: %s — %s", config_path, e)
    return {}


def _env(key: str, default: str = "") -> str:
    """读取环境变量。"""
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    """读取整数环境变量。"""
    val = os.environ.get(key)
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return default


def _try_load_env_file(env_file: Path) -> dict:
    """尝试从 .env 文件加载缺失的值（只读，不覆盖已有环境变量）。"""
    loaded = {}
    if not env_file.exists():
        return loaded

    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                # 只补充环境变量中没有的
                if key and key not in os.environ:
                    loaded[key] = value
    except Exception as e:
        logger.warning(".env 加载失败: %s — %s", env_file, e)

    return loaded


def load_config(wiki_root: Optional[str] = None) -> WikiConfig:
    """
    加载 Wiki Brain 配置。

    优先级：环境变量 > wiki_config.yaml > .env > 默认值
    """
    # 1. 确定 wiki_root
    root = Path(wiki_root or _env("WIKI_ROOT", str(Path.home() / "wiki")))

    # 2. 尝试加载 wiki_config.yaml
    yaml_cfg = _load_yaml_config(root / "wiki_config.yaml")

    # 3. 尝试从 .env 加载缺失值
    env_file = Path(_env("ENV_FILE", str(Path.home() / ".hermes" / ".env")))
    env_overrides = _try_load_env_file(env_file)

    # 4. 合并配置 (yaml < env_overrides < 环境变量)
    def _pick(yaml_key: str, env_key: str, default: str = "") -> str:
        if os.environ.get(env_key):
            return os.environ[env_key]
        if env_overrides.get(env_key):
            return env_overrides[env_key]
        if yaml_cfg.get(yaml_key):
            return str(yaml_cfg[yaml_key])
        return default

    # 5. 构建 WikiConfig
    ov = yaml_cfg.get("openviking", {})
    notion = yaml_cfg.get("notion", {})

    config = WikiConfig(
        wiki_root=root,
        registry_file=root / "registry.json",
        schema_file=root / "SCHEMA.md",
        env_file=env_file,
        # MCP
        mcp_host=_pick("mcp_host", "MCP_HOST", "0.0.0.0"),
        mcp_port=_env_int("MCP_PORT") or yaml_cfg.get("mcp_port", 8764),
        # OpenViking
        openviking_endpoint=_pick("endpoint", "OPENVIKING_ENDPOINT", ov.get("endpoint", "http://localhost:1933")),
        openviking_api_key=_pick("api_key", "OPENVIKING_API_KEY", ov.get("api_key", "")),
        openviking_account=_pick("account", "OPENVIKING_ACCOUNT", ov.get("account", "hermes")),
        openviking_user=_pick("user", "OPENVIKING_USER", ov.get("user", "default")),
        openviking_bin=_env("OPENVIKING_BIN", ""),
        # LLM config removed in v4
        # Notion
        notion_api_key=_pick("api_key", "NOTION_API_KEY", notion.get("api_key", "")),
        webdav_base_url=_pick("webdav_base_url", "WEBDAV_BASE_URL", notion.get("webdav_base_url", "")),
        webdav_user=_pick("webdav_user", "WEBDAV_USER", notion.get("webdav_user", "")),
        webdav_pass=_pick("webdav_pass", "WEBDAV_PASS", notion.get("webdav_pass", "")),
    )

    return config


# === 全局单例（懒加载）===
_config: Optional[WikiConfig] = None


def get_config(wiki_root: Optional[str] = None) -> WikiConfig:
    """获取全局配置单例。"""
    global _config
    if _config is None:
        _config = load_config(wiki_root)
    return _config
