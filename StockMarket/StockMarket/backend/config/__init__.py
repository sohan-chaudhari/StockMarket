import os
from typing import Dict, List, Optional

_CONFIG_CACHE: dict = {}

def load_retention_policy(path: Optional[str] = None) -> dict:
    from config.retention_policy import RETENTION_POLICY
    key = "retention_policy"
    if key not in _CONFIG_CACHE:
        _CONFIG_CACHE[key] = {
            "version": RETENTION_POLICY.get("version", 2),
            "retention_policy": RETENTION_POLICY.get("retention_policy", []),
        }
    return _CONFIG_CACHE[key]

def reload_config():
    _CONFIG_CACHE.clear()
