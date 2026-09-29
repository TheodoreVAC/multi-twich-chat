from __future__ import annotations

from dataclasses import dataclass

from config import DEFAULT_REPEAT_AFTER


@dataclass
class Account:
    username: str
    token: str


@dataclass
class PreparedMessage:
    name: str
    text: str
    cooldown: float
    accounts: list[str]
    repeat_after: float = DEFAULT_REPEAT_AFTER
    enabled: bool = True

    def allows_account(self, username: str) -> bool:
        normalized = username.strip().lower()
        return "*" in self.accounts or normalized in self.accounts


@dataclass
class AccountSettings:
    mode: str = "message"
    flood_running: bool = False
