import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

_ALL_CONNECTORS = ["guardian", "independent", "ipuz"]


class ConfigBase:
    def _to_lines(self, indent: int = 0) -> list[str]:
        prefix = "  " * indent
        lines = []
        for f in fields(self):
            val = getattr(self, f.name)
            if isinstance(val, ConfigBase):
                lines.append(f"{prefix}{f.name}:")
                lines.extend(val._to_lines(indent + 1))
            else:
                lines.append(f"{prefix}{f.name}: {val!r}")
        return lines

    def __str__(self) -> str:
        return "\n".join(self._to_lines())


@dataclass
class ServerConfig(ConfigBase):
    log_level: str = "INFO"


@dataclass
class RoomConfig(ConfigBase):
    ttl_hours: int = 6


@dataclass
class UiConfig(ConfigBase):
    hold_delay_ms: int = 300
    hold_drift_px: int = 8


@dataclass
class ConnectorsConfig(ConfigBase):
    enabled: list = field(default_factory=lambda: list(_ALL_CONNECTORS))
    gated: list = field(default_factory=list)
    password: str | None = None


@dataclass
class Config(ConfigBase):
    server: ServerConfig = field(default_factory=ServerConfig)
    room: RoomConfig = field(default_factory=RoomConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    connectors: ConnectorsConfig = field(default_factory=ConnectorsConfig)
    source: str = field(default="defaults", init=False, compare=False)


def _apply_dict(section_obj: Any, data: dict) -> None:
    for f in fields(section_obj):
        if f.name not in data:
            continue
        existing = getattr(section_obj, f.name)
        if isinstance(existing, list):
            setattr(section_obj, f.name, list(data[f.name]))
        elif existing is None or data[f.name] is None:
            setattr(section_obj, f.name, data[f.name])
        else:
            setattr(section_obj, f.name, type(existing)(data[f.name]))


def _apply_env(section_name: str, section_obj: Any) -> None:
    prefix = f"VIBEWORDS_{section_name.upper()}_"
    for f in fields(section_obj):
        env_key = prefix + f.name.upper()
        val = os.environ.get(env_key)
        if val is None:
            continue
        existing = getattr(section_obj, f.name)
        if isinstance(existing, list):
            setattr(section_obj, f.name, [s.strip() for s in val.split(',') if s.strip()])
        elif existing is None:
            setattr(section_obj, f.name, val)
        else:
            setattr(section_obj, f.name, type(existing)(val))


def load_config() -> Config:
    cfg = Config()

    config_path_env = os.environ.get("VIBEWORDS_CONFIG")
    candidates = ([Path(config_path_env)] if config_path_env else []) + [Path("config.yaml")]

    raw: dict = {}
    for path in candidates:
        if path.exists():
            with path.open() as f:
                raw = yaml.safe_load(f) or {}
            cfg.source = str(path.resolve())
            break

    sections = {"server": cfg.server, "room": cfg.room, "ui": cfg.ui, "connectors": cfg.connectors}
    for name, obj in sections.items():
        if name in raw and isinstance(raw[name], dict):
            _apply_dict(obj, raw[name])
        _apply_env(name, obj)

    cfg.server.log_level = cfg.server.log_level.upper()
    return cfg
