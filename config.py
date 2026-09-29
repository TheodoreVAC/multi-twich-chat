from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
SETTINGS_FILE = DATA_DIR / "account_settings.json"
FLOOD_HISTORY_FILE = DATA_DIR / "flood_history.json"
MESSAGES_FILE = APP_DIR / "messages.txt"

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
MAX_MESSAGE_LENGTH = 500
MIN_SCALE = 0.80
MAX_SCALE = 1.40
SCALE_STEP = 0.10
MIN_FLOOD_COOLDOWN = 3.0
DEFAULT_REPEAT_AFTER = 3 * 60 * 60
