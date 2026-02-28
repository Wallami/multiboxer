from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_DIR = Path.home() / ".multiboxer"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    game_exe_path: str = ""
    resolution: str = "1600x900"
    session_1_id: str = ""
    session_2_id: str = ""
    session_3_id: str = ""


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return AppConfig()

        return AppConfig(
            game_exe_path=raw.get("game_exe_path", ""),
            resolution=raw.get("resolution", "1600x900"),
            session_1_id=raw.get("session_1_id", ""),
            session_2_id=raw.get("session_2_id", ""),
            session_3_id=raw.get("session_3_id", ""),
        )

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(config), handle, indent=2)
