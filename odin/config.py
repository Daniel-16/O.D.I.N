"""Configuration loading for ODIN."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"

_DEFAULTS: dict[str, Any] = {
    "audio": {"device": "", "sample_rate": 16000, "block_ms": 125},
    "wake": {
        "phrases": ["hey odin", "hi odin"],
        "allow_bare_name": False,
        "on_partial": True,
        "refractory_s": 1.5,
        "settle_ms": 300,
        "extra_decoys": [],
    },
    "listen": {
        "max_s": 6.0,
        "silence_end_s": 0.8,
        "no_speech_timeout_s": 3.0,
        "energy_threshold": 300,
        "alternatives": 5,
        "follow_up_s": 4.0,
        "follow_up_threshold_scale": 2.0,
        "max_turns": 5,
        "clarify": True,
        "clarify_cutoff": 0.62,
    },
    "speech": {
        "enabled": True,
        "verbosity": "chatty",
        "voice": "models/en_US-ryan-medium.onnx",
        "preload": True,
        "beeps": True,
        "volume": 0.5,
    },
    "media": {"players": "spotify,vlc,chromium,firefox,%any"},
    "volume": {"step": 5, "limit": 1.0},
    "brightness": {"step": 10},
    "system": {"screenshot_dir": "~/Pictures", "confirm_destructive": True},
    "apps": {},
    "folders": {},
    "web": {},
    "log": {"level": "info", "transcripts": True},
}


class Config:
    """Merged view of the defaults and the user's config.toml."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.path = path
        self._data: dict[str, Any] = {}
        for section, defaults in _DEFAULTS.items():
            merged = dict(defaults)
            merged.update(data.get(section, {}))
            self._data[section] = merged
        # Keep any section the user added that we do not know about.
        for section, values in data.items():
            self._data.setdefault(section, values)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        target = Path(path).expanduser() if path else DEFAULT_CONFIG
        if not target.exists():
            return cls({}, None)
        with open(target, "rb") as handle:
            return cls(tomllib.load(handle), target)

    def __getitem__(self, section: str) -> dict[str, Any]:
        return self._data[section]

    def get(self, section: str, default: Any = None) -> Any:
        return self._data.get(section, default)

    def __contains__(self, section: str) -> bool:
        return section in self._data

    @property
    def model_dir(self) -> Path:
        return ROOT / "models" / "vosk-model-small-en-us-0.15"

    @property
    def voice_path(self) -> Path:
        voice = Path(self["speech"]["voice"]).expanduser()
        return voice if voice.is_absolute() else ROOT / voice

    @property
    def screenshot_dir(self) -> Path:
        return Path(self["system"]["screenshot_dir"]).expanduser()
