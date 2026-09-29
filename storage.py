from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path

from models import Account, AccountSettings


class AccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[Account]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        if not isinstance(data, list):
            return []

        result: list[Account] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username", "")).strip().lower()
            token = str(item.get("token", "")).strip()
            if username and token:
                result.append(Account(username=username, token=token))
        return result

    def save(self, accounts: list[Account]) -> None:
        self.path.write_text(
            json.dumps([asdict(account) for account in accounts], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class AccountSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, AccountSettings]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if not isinstance(data, dict):
            return {}

        result: dict[str, AccountSettings] = {}
        for username, raw in data.items():
            if not isinstance(raw, dict):
                continue
            mode = str(raw.get("mode", "message")).strip().lower()
            if mode not in {"message", "flood"}:
                mode = "message"
            result[username.lower()] = AccountSettings(mode=mode, flood_running=False)
        return result

    def save(self, settings: dict[str, AccountSettings]) -> None:
        payload = {
            username: {
                "mode": value.mode,
                "flood_running": False,
            }
            for username, value in settings.items()
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class FloodHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self) -> dict[str, dict[str, float]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if not isinstance(data, dict):
            return {}

        result: dict[str, dict[str, float]] = {}
        for username, raw in data.items():
            if not isinstance(raw, dict):
                continue
            history: dict[str, float] = {}
            for preset_name, timestamp in raw.items():
                try:
                    history[str(preset_name)] = float(timestamp)
                except (TypeError, ValueError):
                    continue
            result[str(username).lower()] = history
        return result

    def save(self, history: dict[str, dict[str, float]]) -> None:
        with self._lock:
            self.path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    def mark_sent(self, username: str, preset_name: str, timestamp: float) -> None:
        with self._lock:
            username = username.lower()
            bucket = history.setdefault(username, {})
            bucket[preset_name] = timestamp
            self.path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    def remove_account(self, username: str) -> None:
        with self._lock:
            history.pop(username.lower(), None)
            self.path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
