"""
profiles.py — Named configuration profile management.

Profiles are YAML snapshots of the full config saved in the profiles/ directory.
Load, save, and delete from the GUI or programmatically.

The complete config state is saved: detection, cursor_follow, tracking, overlay,
triggerbot, hotkeys. The runtime-only 'presets' key is stripped on save.
"""

import copy
import logging
import os
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

PROFILES_DIR = "profiles"

# Keys that are stripped from saved profiles (runtime-only, not user config)
_RUNTIME_KEYS = {"presets"}


def _ensure_dir() -> None:
    os.makedirs(PROFILES_DIR, exist_ok=True)


def list_profiles() -> List[str]:
    """Return sorted list of saved profile names (without .yaml extension)."""
    _ensure_dir()
    return sorted(
        f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".yaml")
    )


def save_profile(name: str, config: dict) -> None:
    """Save current config as a named profile. Strips runtime-only keys."""
    _ensure_dir()
    to_save = copy.deepcopy(config)
    for key in _RUNTIME_KEYS:
        to_save.pop(key, None)
    path = os.path.join(PROFILES_DIR, f"{name}.yaml")
    with open(path, "w") as f:
        yaml.dump(to_save, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    logger.info("Profile saved: '%s'", name)


def load_profile(name: str) -> dict:
    """Load a profile by name and return its complete config dict."""
    path = os.path.join(PROFILES_DIR, f"{name}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Profile not found: {name}")
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    logger.info("Profile loaded: '%s'", name)
    return data


def delete_profile(name: str) -> None:
    """Delete a profile by name."""
    path = os.path.join(PROFILES_DIR, f"{name}.yaml")
    if os.path.exists(path):
        os.remove(path)
        logger.info("Profile deleted: '%s'", name)


class ProfileManager:
    """
    Convenience class wrapping module-level profile functions.
    Holds a reference to the live config and provides save/load/delete/list.
    """

    def __init__(self, config: Optional[dict] = None):
        self._config = config

    def set_config(self, config: dict) -> None:
        self._config = config

    def list(self) -> List[str]:
        return list_profiles()

    def save(self, name: str, config: Optional[dict] = None) -> None:
        cfg = config if config is not None else self._config
        if cfg is None:
            raise ValueError("No config provided to save.")
        save_profile(name, cfg)

    def load(self, name: str) -> dict:
        return load_profile(name)

    def delete(self, name: str) -> None:
        delete_profile(name)

    def apply(self, name: str) -> dict:
        """Load profile and merge it into the held config. Returns the loaded data."""
        data = load_profile(name)
        if self._config is not None:
            for section, vals in data.items():
                if isinstance(vals, dict):
                    self._config.setdefault(section, {}).update(vals)
                else:
                    self._config[section] = vals
        return data
