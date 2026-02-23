"""Per-project configuration (custom instructions) stored in ~/.conn/projects/."""
from __future__ import annotations

import json
from pathlib import Path

from .config import PROJECTS_CONFIG_DIR


def _config_file(project_path: str) -> Path:
    """Map a project directory path to its config JSON file."""
    name = Path(project_path).name
    return PROJECTS_CONFIG_DIR / f"{name}.json"


def get_project_config(project_path: str) -> dict:
    """Read config for a project. Returns dict with path and custom_instructions."""
    cfg = _config_file(project_path)
    if cfg.exists():
        with open(cfg) as f:
            return json.load(f)
    return {"path": project_path, "custom_instructions": ""}


def get_custom_instructions(project_path: str) -> str | None:
    """Return custom instructions for a project, or None if empty/unset."""
    config = get_project_config(project_path)
    instructions = config.get("custom_instructions", "").strip()
    return instructions if instructions else None


def set_custom_instructions(project_path: str, instructions: str):
    """Write custom instructions for a project."""
    PROJECTS_CONFIG_DIR.mkdir(exist_ok=True)
    cfg = _config_file(project_path)
    data = {"path": project_path, "custom_instructions": instructions}
    with open(cfg, "w") as f:
        json.dump(data, f, indent=2)
