"""
profiles.py — Named configuration profile management.

Profiles are YAML snapshots of the full config saved in the profiles/ directory.
Load, save, and delete from the GUI or programmatically.
"""

import copy
import logging
import os
from typing import List

import yaml

logger = logging.getLogger(__name__)

PROFILES_DIR = "profiles"


def _ensure_dir() -> None:
    os.makedirs(PROFILES_DIR, exist_ok=True)


def list_profiles() -> List[str]:
    """Return sorted list of saved profile names (without .yaml extension)."""
    _ensure_dir()
    return sorted(
        f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".yaml")
    )


def save_profile(name: str, config: dict) -> None:
    """Save current config as a named profile. Strips runtime-only 'presets' key."""
    _ensure_dir()
    to_save = copy.deepcopy(config)
    to_save.pop("presets", None)
    path = os.path.join(PROFILES_DIR, f"{name}.yaml")
    with open(path, "w") as f:
        yaml.dump(to_save, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    logger.info("Profile saved: '%s'", name)


def load_profile(name: str) -> dict:
    """Load a profile by name and return its config dict."""
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