import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ServerConfig:
    log_level: str = "INFO"


@dataclass
class RoomConfig:
    ttl_hours: int = 6


@dataclass
class UiConfig:
    hold_delay_ms: int = 300
    hold_drift_px: int = 8


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    room: RoomConfig = field(default_factory=RoomConfig)
    ui: UiConfig = field(default_factory=UiConfig)


def _apply_dict(section_obj: Any, data: dict) -> None:
    for f in fields(section_obj):
        if f.name in data:
            setattr(section_obj, f.name, type(getattr(section_obj, f.name))(data[f.name]))


def _apply_env(section_name: str, section_obj: Any) -> None:
    prefix = f"VIBEWORD_{section_name.upper()}_"
    for f in fields(section_obj):
        env_key = prefix + f.name.upper()
        val = os.environ.get(env_key)
        if val is not None:
            setattr(section_obj, f.name, type(getattr(section_obj, f.name))(val))


def load_config() -> Config:
    cfg = Config()

    config_path_env = os.environ.get("VIBEWORD_CONFIG")
    candidates = ([Path(config_path_env)] if config_path_env else []) + [Path("config.yaml")]

    raw: dict = {}
    for path in candidates:
        if path.exists():
            with path.open() as f:
                raw = yaml.safe_load(f) or {}
            break

    sections = {"server": cfg.server, "room": cfg.room, "ui": cfg.ui}
    for name, obj in sections.items():
        if name in raw and isinstance(raw[name], dict):
            _apply_dict(obj, raw[name])
        _apply_env(name, obj)

    cfg.server.log_level = cfg.server.log_level.upper()
    return cfg
