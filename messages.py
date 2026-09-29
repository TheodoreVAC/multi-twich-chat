from __future__ import annotations

from pathlib import Path
from typing import Optional

from config import DEFAULT_REPEAT_AFTER, MIN_FLOOD_COOLDOWN
from models import PreparedMessage


class PreparedMessageStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.ensure_file()

    def ensure_file(self) -> None:
        if self.path.exists():
            return
        example = """# Twitch Multi Chat prepared messages
#
# [name]                 Unique preset name.
# text=                  Message to send.
# cooldown=              Delay after sending this message.
# accounts=              * for all accounts, or comma-separated usernames.
# repeat_after=          Minimum time before this exact message can be used again.
# enabled=               true/false.
#
# Flood mode picks messages randomly from all enabled presets allowed for
# the selected account. A message is not picked again until repeat_after expires.
# The send history is stored in data/flood_history.json so restarting the app
# does not reset the repeat protection.

[greeting]
text=Hello chat!
cooldown=15
accounts=*
repeat_after=3h
enabled=true

[hello]
text=Hello everyone!
cooldown=20
accounts=*
repeat_after=3h
enabled=true

[thanks]
text=Thanks for watching!
cooldown=25
accounts=*
repeat_after=3h
enabled=true
"""
        self.path.write_text(example, encoding="utf-8")

    @staticmethod
    def _parse_duration(value: str, default: float) -> float:
        raw = value.strip().lower().replace(" ", "")
        if not raw:
            return default
        units = (
            ("ms", 0.001),
            ("s", 1.0),
            ("m", 60.0),
            ("h", 3600.0),
            ("d", 86400.0),
        )
        for suffix, multiplier in units:
            if raw.endswith(suffix):
                try:
                    return max(0.0, float(raw[: -len(suffix)]) * multiplier)
                except ValueError:
                    return default
        try:
            return max(0.0, float(raw))
        except ValueError:
            return default

    def load(self) -> list[PreparedMessage]:
        self.ensure_file()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        result: list[PreparedMessage] = []
        current_name: Optional[str] = None
        current: dict[str, str] = {}

        def flush() -> None:
            nonlocal current_name, current
            if not current_name:
                current = {}
                return

            text = current.get("text", "").strip()
            if not text:
                current = {}
                return

            cooldown = max(
                MIN_FLOOD_COOLDOWN,
                self._parse_duration(current.get("cooldown", "15"), 15.0),
            )
            repeat_after = max(
                0.0,
                self._parse_duration(current.get("repeat_after", "3h"), DEFAULT_REPEAT_AFTER),
            )
            accounts_raw = current.get("accounts", "*").strip()
            accounts = [x.strip().lower() for x in accounts_raw.split(",") if x.strip()] or ["*"]
            enabled = current.get("enabled", "true").strip().lower() in {"1", "true", "yes", "on"}

            result.append(
                PreparedMessage(
                    name=current_name,
                    text=text,
                    cooldown=cooldown,
                    accounts=accounts,
                    repeat_after=repeat_after,
                    enabled=enabled,
                )
            )
            current = {}

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                flush()
                current_name = line[1:-1].strip()
                continue
            if "=" in line and current_name:
                key, value = line.split("=", 1)
                current[key.strip().lower()] = value.strip()

        flush()
        return result
