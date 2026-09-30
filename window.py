from __future__ import annotations

import json
import random
import threading
import time
from typing import Optional

from PySide6.QtCore import QTimer, Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from config import (
    ACCOUNTS_FILE,
    DEFAULT_REPEAT_AFTER,
    FLOOD_ACCOUNT_GAP,
    FLOOD_HISTORY_FILE,
    MAX_SCALE,
    MIN_SCALE,
    MESSAGES_FILE,
    MAX_MESSAGE_LENGTH,
    SCALE_STEP,
    SETTINGS_FILE,
)
from dialogs import AddAccountDialog
from messages import PreparedMessageStore
from models import Account, AccountSettings, PreparedMessage
from storage import AccountSettingsStore, AccountStore, FloodHistoryStore
from twitch import SignalBridge, TwitchIRCClient


class MainWindow(QMainWindow):
    ACCOUNT_COLORS = [
        "#79c0ff", "#d2a8ff", "#7ee787", "#ffa657", "#ff7b72",
        "#56d4dd", "#f2cc60", "#a5d6ff", "#c8a1ff", "#8ddb8c",
        "#ff9bce", "#70e1f5",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Twitch Multi Chat")
        self.resize(1220, 780)
        self.setMinimumSize(980, 620)

        self.account_store = AccountStore(ACCOUNTS_FILE)
        self.settings_store = AccountSettingsStore(SETTINGS_FILE)
        self.history_store = FloodHistoryStore(FLOOD_HISTORY_FILE)
        self.message_store = PreparedMessageStore(MESSAGES_FILE)

        global history
        history = self.history_store.load()
        shared_history = history.setdefault("__shared__", {})
        # Migrate old per-account history conservatively: one account's recent
        # send prevents every other account from reusing that preset as well.
        for username, bucket in history.items():
            if username == "__shared__":
                continue
            for preset_name, timestamp in bucket.items():
                shared_history[preset_name] = max(
                    float(shared_history.get(preset_name, 0.0)), float(timestamp)
                )

        self.accounts: dict[str, Account] = {a.username: a for a in self.account_store.load()}
        self.account_settings: dict[str, AccountSettings] = self.settings_store.load()
        for username in self.accounts:
            self.account_settings.setdefault(username, AccountSettings())

        self.prepared_messages: list[PreparedMessage] = []
        self.clients: dict[str, TwitchIRCClient] = {}
        self.statuses: dict[str, str] = {}
        self.account_notices: dict[str, str] = {}
        self.selected_username: Optional[str] = None
        self.current_channel: Optional[str] = None
        self.scale = 1.1
        self.flood_threads: dict[str, threading.Thread] = {}
        self.flood_stops: dict[str, threading.Event] = {}
        self.flood_send_lock = threading.Lock()
        self.last_flood_send_at = 0.0
        self.recent_message_ids: set[str] = set()
        self.recent_message_order: list[str] = []
        self.pending_sent_messages: dict[str, tuple[str, float]] = {}
        self.logs_dialog: Optional[QDialog] = None
        self.logs_view: Optional[QTextBrowser] = None
        self.log_history: list[str] = []
        self.connection_queue: list[Account] = []
        self.connection_timer = QTimer(self)
        self.connection_timer.setSingleShot(True)
        self.connection_timer.timeout.connect(self._connect_next_queued_account)

        self.bridge = SignalBridge()
        self.bridge.message_received.connect(self._handle_message)
        self.bridge.connection_status.connect(self._handle_status)
        self.bridge.connection_error.connect(self._handle_error)
        self.bridge.log_message.connect(self._append_log)
        self.bridge.broadcast_finished.connect(self._broadcast_finished)

        self._build_ui()
        self.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self._load_prepared_messages(False)
        self._apply_style()
        self._refresh_accounts()
        self._show_account_settings()
        self._log("[APP] Twitch Multi Chat started")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("TWITCH MULTI CHAT")
        title.setObjectName("title")
        header.addWidget(title)
        header.addSpacing(15)

        channel_label = QLabel("CHANNEL")
        channel_label.setObjectName("secondary")
        header.addWidget(channel_label)
        self.channel_edit = QLineEdit()
        self.channel_edit.setPlaceholderText("Search channel...")
        self.channel_edit.setClearButtonEnabled(True)
        self.channel_edit.setFixedWidth(220)
        self.channel_edit.returnPressed.connect(self._connect_all)
        self.channel_edit.textChanged.connect(self._update_connect_button)
        header.addWidget(self.channel_edit)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._connect_all)
        header.addWidget(self.connect_button)

        header.addStretch(1)

        self.refresh_button = QPushButton("↻ Reconnect")
        self.refresh_button.setToolTip("Reconnect the selected account, or all accounts when none is selected")
        self.refresh_button.clicked.connect(self._refresh_selected_or_all)
        header.addWidget(self.refresh_button)

        self.logs_button = QPushButton("IRC Log")
        self.logs_button.setObjectName("terminalButton")
        self.logs_button.setToolTip("Open or close the full activity terminal")
        self.logs_button.clicked.connect(self._toggle_logs)
        header.addWidget(self.logs_button)

        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.clicked.connect(lambda: self._change_scale(-SCALE_STEP))
        header.addWidget(self.zoom_out_button)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setMinimumWidth(50)
        header.addWidget(self.zoom_label)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.clicked.connect(lambda: self._change_scale(SCALE_STEP))
        header.addWidget(self.zoom_in_button)

        self.add_button = QPushButton("+ Add")
        self.add_button.clicked.connect(self._add_account)
        header.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_account)
        header.addWidget(self.remove_button)
        root.addLayout(header)

        broadcast_row = QHBoxLayout()
        broadcast_label = QLabel("ALL BOTS")
        broadcast_label.setObjectName("section")
        broadcast_row.addWidget(broadcast_label)
        self.broadcast_edit = QLineEdit()
        self.broadcast_edit.setPlaceholderText("Send a message from every connected account…")
        self.broadcast_edit.setMaximumWidth(430)
        self.broadcast_edit.returnPressed.connect(self._send_broadcast_message)
        broadcast_row.addWidget(self.broadcast_edit, 1)
        self.broadcast_interval = QSpinBox()
        self.broadcast_interval.setRange(0, 5000)
        self.broadcast_interval.setSingleStep(10)
        self.broadcast_interval.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.broadcast_interval.setSuffix(" ms")
        self.broadcast_interval.setValue(100)
        self.broadcast_interval.setFixedWidth(74)
        self.broadcast_interval.setToolTip("Delay between account sends; 100 ms by default")
        broadcast_row.addWidget(self.broadcast_interval)
        self.broadcast_button = QPushButton("Send to all")
        self.broadcast_button.setFixedWidth(100)
        self.broadcast_button.clicked.connect(self._send_broadcast_message)
        broadcast_row.addWidget(self.broadcast_button)
        root.addLayout(broadcast_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        chat_panel = QWidget()
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(0, 0, 4, 0)
        self.chat_header = QLabel("Enter a channel above")
        self.chat_header.setObjectName("section")
        chat_layout.addWidget(self.chat_header)
        self.chat_list = QTextBrowser()
        self.chat_list.setReadOnly(True)
        self.chat_list.setOpenExternalLinks(False)
        self.chat_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chat_list.setUndoRedoEnabled(False)
        self.chat_list.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.chat_list.document().setDocumentMargin(8)
        self.chat_list.document().setDefaultStyleSheet("body { margin: 0px; padding: 0px; }")
        chat_layout.addWidget(self.chat_list, 1)
        splitter.addWidget(chat_panel)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(7)

        accounts_header = QHBoxLayout()
        accounts_label = QLabel("ACCOUNTS")
        accounts_label.setObjectName("section")
        accounts_header.addWidget(accounts_label)
        accounts_header.addStretch(1)

        self.account_up_button = QPushButton("▲")
        self.account_up_button.setToolTip("Previous account")
        self.account_up_button.setFixedWidth(38)
        self.account_up_button.clicked.connect(lambda: self._move_account_selection(-1))
        accounts_header.addWidget(self.account_up_button)

        self.account_down_button = QPushButton("▼")
        self.account_down_button.setToolTip("Next account")
        self.account_down_button.setFixedWidth(38)
        self.account_down_button.clicked.connect(lambda: self._move_account_selection(1))
        accounts_header.addWidget(self.account_down_button)

        right_layout.addLayout(accounts_header)

        self.account_list = QListWidget()
        self.account_list.currentItemChanged.connect(self._account_selected)
        self.account_list.setSpacing(2)
        self.account_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right_layout.addWidget(self.account_list, 1)

        mode_label = QLabel("ACCOUNT MODE")
        mode_label.setObjectName("section")
        right_layout.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Message", "message")
        self.mode_combo.addItem("Flood", "flood")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        right_layout.addWidget(self.mode_combo)

        connection_delay_label = QLabel("CONNECTION DELAY")
        connection_delay_label.setObjectName("section")
        right_layout.addWidget(connection_delay_label)

        self.connection_delay_spin = QDoubleSpinBox()
        self.connection_delay_spin.setRange(0.0, 3600.0)
        self.connection_delay_spin.setSingleStep(0.5)
        self.connection_delay_spin.setDecimals(1)
        self.connection_delay_spin.setSuffix(" s")
        self.connection_delay_spin.setToolTip(
            "Delay before this account joins the selected channel during a multi-account connection."
        )
        self.connection_delay_spin.valueChanged.connect(self._connection_delay_changed)
        right_layout.addWidget(self.connection_delay_spin)

        self.flood_panel = QWidget()
        flood_layout = QVBoxLayout(self.flood_panel)
        flood_layout.setContentsMargins(0, 0, 0, 0)
        flood_layout.setSpacing(6)
        flood_label = QLabel("FLOOD")
        flood_label.setObjectName("section")
        flood_layout.addWidget(flood_label)

        self.flood_info = QLabel("")
        self.flood_info.setObjectName("secondary")
        self.flood_info.setWordWrap(True)
        flood_layout.addWidget(self.flood_info)

        self.flood_toggle = QPushButton("Start Flood")
        self.flood_toggle.clicked.connect(self._toggle_flood)
        flood_layout.addWidget(self.flood_toggle)
        right_layout.addWidget(self.flood_panel)

        self.messages_reload_button = QPushButton("↻ Reload messages.txt")
        self.messages_reload_button.clicked.connect(lambda: self._load_prepared_messages(True))
        right_layout.addWidget(self.messages_reload_button)

        splitter.addWidget(right)
        splitter.setSizes([860, 330])
        root.addWidget(splitter, 1)

        composer = QHBoxLayout()
        self.selected_label = QLabel("No account selected")
        self.selected_label.setObjectName("selectedAccount")
        self.selected_label.setMinimumWidth(170)
        composer.addWidget(self.selected_label)

        self.message_edit = QLineEdit()
        self.message_edit.setPlaceholderText("Enter a channel and select an account...")
        self.message_edit.returnPressed.connect(self._send_manual_message)
        composer.addWidget(self.message_edit, 1)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_manual_message)
        composer.addWidget(self.send_button)
        root.addLayout(composer)

        self._update_connect_button()

    def _apply_style(self) -> None:
        s = self.scale
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{ background: #101216; color: #e6e9ef; font-size: {max(10, round(13*s))}px; }}
            QLabel#title {{ font-size: {max(12, round(16*s))}px; font-weight: 700; letter-spacing: 1px; }}
            QLabel#section {{ font-size: {max(9, round(11*s))}px; font-weight: 700; color: #7f8796; padding: 2px 2px; }}
            QLabel#secondary {{ color: #7f8796; }}
            QLabel#selectedAccount {{ color: #aeb6c4; font-weight: 600; padding-left: 3px; }}
            QLabel#zoomLabel {{ color: #aeb6c4; font-weight: 700; }}
            QLineEdit, QListWidget, QTextBrowser, QComboBox, QDoubleSpinBox {{ background: #171a20; border: 1px solid #292e38; border-radius: 7px; color: #e6e9ef; }}
            QLineEdit {{ padding: {max(7, round(9*s))}px 10px; }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid #5865f2; }}
            QListWidget {{ padding: 4px; }}
            QListWidget::item {{ padding: 0px; margin: 0px; border-radius: 6px; }}
            QListWidget::item:selected {{ background: transparent; color: #ffffff; border: none; outline: none; }}
            QTextBrowser {{ background: #12151a; padding: 0px; selection-background-color: #2b3340; selection-color: #ffffff; border: 1px solid #292e38; }}
            QPushButton {{ background: #1b1f27; border: 1px solid #2c323d; border-radius: 7px; padding: {max(6, round(8*s))}px {max(9, round(13*s))}px; color: #e6e9ef; font-weight: 600; }}
            QPushButton:hover {{ background: #242a34; }}
            QPushButton#terminalButton {{ border-color: #d8dce5; color: #ffffff; }}
            QPushButton:pressed {{ background: #15181e; }}
            QPushButton:disabled {{ color: #5e6572; background: #15181d; }}
            QComboBox, QDoubleSpinBox {{ padding: {max(7, round(9*s))}px 10px; }}
            QComboBox QAbstractItemView {{ background: #171a20; border: 1px solid #303641; color: #e6e9ef; selection-background-color: #252b36; selection-color: #ffffff; padding: 4px; }}
            QScrollBar:vertical {{ background: #14171c; width: {max(8, round(11*s))}px; margin: 3px 1px; border: none; }}
            QScrollBar::handle:vertical {{ background: #3a414d; min-height: {max(28, round(34*s))}px; border-radius: {max(4, round(6*s))}px; }}
            QScrollBar::handle:vertical:hover {{ background: #596273; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; border: none; }}
            QScrollBar:horizontal {{ background: #14171c; height: {max(8, round(11*s))}px; margin: 1px 3px; border: none; }}
            QScrollBar::handle:horizontal {{ background: #3a414d; min-width: {max(28, round(34*s))}px; border-radius: {max(4, round(6*s))}px; }}
            QScrollBar::handle:horizontal:hover {{ background: #596273; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; border: none; }}
            """
        )
        chat_font = QFont("Sans")
        chat_font.setPointSizeF(max(10.5, 12.0 * s))
        self.chat_list.setFont(chat_font)
        self.account_list.setFont(chat_font)
        self.chat_list.document().setDefaultFont(chat_font)
        self.zoom_label.setText(f"{round(self.scale*100)}%")
        self.zoom_out_button.setEnabled(self.scale > MIN_SCALE)
        self.zoom_in_button.setEnabled(self.scale < MAX_SCALE)

    def _change_scale(self, delta: float) -> None:
        self.scale = max(MIN_SCALE, min(MAX_SCALE, round(self.scale + delta, 2)))
        self._apply_style()
        self._refresh_accounts()
        scaled_font = QFont("Sans")
        scaled_font.setPointSizeF(max(10.5, 12.0 * self.scale))
        self.chat_list.document().setDefaultFont(scaled_font)
        cursor = self.chat_list.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setFont(scaled_font)
        cursor.mergeCharFormat(fmt)
        self._update_account_selection_visuals()

    def _load_prepared_messages(self, show_status: bool = True) -> None:
        self.prepared_messages = self.message_store.load()
        self._update_flood_info()
        if show_status:
            self.statusBar().showMessage(
                f"Reloaded messages.txt: {len(self.prepared_messages)} enabled/defined presets loaded",
                2000,
            )

    def _get_eligible_presets(self, username: str) -> list[PreparedMessage]:
        return [
            preset
            for preset in self.prepared_messages
            if preset.enabled and preset.allows_account(username) and preset.text
        ]

    def _update_flood_info(self) -> None:
        username = self.selected_username
        if not username:
            self.flood_info.setText("")
            return
        eligible = self._get_eligible_presets(username)
        if not eligible:
            self.flood_info.setText("No enabled messages are assigned to this account.")
            return
        repeat_values = {preset.repeat_after for preset in eligible}
        min_cooldown = min(preset.cooldown for preset in eligible)
        if len(repeat_values) == 1:
            repeat_text = self._format_duration(next(iter(repeat_values)))
            self.flood_info.setText(
                f"{len(eligible)} messages · random order · no repeats · repeat after {repeat_text}+ · min delay {min_cooldown:g}s"
            )
        else:
            self.flood_info.setText(
                f"{len(eligible)} messages · random order · no repeats · each message has its own repeat delay"
            )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0.0, seconds)
        if seconds < 60:
            return f"{seconds:g}s"
        if seconds < 3600:
            return f"{seconds / 60:g}m"
        if seconds < 86400:
            return f"{seconds / 3600:g}h"
        return f"{seconds / 86400:g}d"

    def _account_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self.selected_username = str(current.data(Qt.ItemDataRole.UserRole)) if current else None
        self._update_account_selection_visuals()
        self._show_account_settings()
        QTimer.singleShot(0, self._focus_message_input)

    def _move_account_selection(self, delta: int) -> None:
        count = self.account_list.count()
        if count == 0:
            return
        row = self.account_list.currentRow()
        if row < 0:
            row = 0 if delta > 0 else count - 1
        else:
            row = (row + delta) % count
        self.account_list.setCurrentRow(row)
        self.account_list.scrollToItem(
            self.account_list.currentItem(),
            QListWidget.ScrollHint.PositionAtCenter,
        )
        QTimer.singleShot(0, self._focus_message_input)

    def _account_name_color(self, username: str) -> str:
        index = sum((i + 1) * ord(char) for i, char in enumerate(username.lower())) % len(self.ACCOUNT_COLORS)
        return self.ACCOUNT_COLORS[index]

    def _update_account_selection_visuals(self) -> None:
        selected = self.selected_username
        for row in range(self.account_list.count()):
            item = self.account_list.item(row)
            username = str(item.data(Qt.ItemDataRole.UserRole))
            widget = self.account_list.itemWidget(item)
            if widget is None:
                continue
            name_label = widget.findChild(QLabel, "accountName")
            if name_label is not None:
                if username == selected:
                    name_label.setStyleSheet(
                        f"font-weight: 750; color: {self._account_name_color(username)}; background: transparent;"
                    )
                else:
                    name_label.setStyleSheet(
                        "font-weight: 650; color: #e6e9ef; background: transparent;"
                    )
            widget.setStyleSheet("QWidget { background: transparent; border: none; }")

    def _focus_message_input(self) -> None:
        if self.selected_username and self.account_settings.get(self.selected_username, AccountSettings()).mode == "message":
            self.message_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            self.message_edit.clear()

    def _show_account_settings(self) -> None:
        username = self.selected_username
        if not username:
            self.selected_label.setText("No account selected")
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(0)
            self.mode_combo.blockSignals(False)
            self.flood_panel.setVisible(False)
            self.message_edit.setEnabled(False)
            self.send_button.setEnabled(False)
            self.connection_delay_spin.blockSignals(True)
            self.connection_delay_spin.setValue(0.0)
            self.connection_delay_spin.blockSignals(False)
            self.connection_delay_spin.setEnabled(False)
            self._update_flood_info()
            return

        self.selected_label.setText(f"[{username}]")
        settings = self.account_settings.setdefault(username, AccountSettings())
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(1 if settings.mode == "flood" else 0)
        self.mode_combo.blockSignals(False)
        self.connection_delay_spin.blockSignals(True)
        self.connection_delay_spin.setValue(max(0.0, min(3600.0, float(settings.connect_delay))))
        self.connection_delay_spin.blockSignals(False)
        self.connection_delay_spin.setEnabled(True)
        self._update_flood_info()
        self._update_status_label()
        self._update_mode_ui()

    def _connection_delay_changed(self, value: float) -> None:
        username = self.selected_username
        if not username:
            return
        settings = self.account_settings.setdefault(username, AccountSettings())
        settings.connect_delay = round(max(0.0, min(3600.0, float(value))), 1)
        self.settings_store.save(self.account_settings)
        self._log(f"[SETTINGS] {username}: connection delay = {settings.connect_delay:g}s")

    def _mode_changed(self, _index: int) -> None:
        username = self.selected_username
        if not username:
            return
        settings = self.account_settings.setdefault(username, AccountSettings())
        settings.mode = str(self.mode_combo.currentData() or "message")
        if settings.mode != "flood":
            self._stop_flood(username)
        self.settings_store.save(self.account_settings)
        self._update_mode_ui()
        if settings.mode == "message":
            QTimer.singleShot(0, self._focus_message_input)

    def _update_mode_ui(self) -> None:
        username = self.selected_username
        if not username:
            return
        settings = self.account_settings.setdefault(username, AccountSettings())
        flood_mode = settings.mode == "flood"
        self.flood_panel.setVisible(flood_mode)
        connected = self.statuses.get(username) == "CONNECTED"
        self.message_edit.setEnabled(connected and not flood_mode)
        self.send_button.setEnabled(connected and not flood_mode)
        self.flood_toggle.setEnabled(connected and bool(self._get_eligible_presets(username)))
        self.flood_toggle.setText("Stop Flood" if settings.flood_running else "Start Flood")
        self.message_edit.setPlaceholderText(
            f"Message as {username}..." if connected and not flood_mode else
            ("Flood mode is active" if flood_mode else "Account is not connected...")
        )

    def _toggle_flood(self) -> None:
        username = self.selected_username
        if not username:
            return
        settings = self.account_settings.setdefault(username, AccountSettings())
        if settings.flood_running:
            self._stop_flood(username)
        else:
            self._start_flood(username)
        self._update_mode_ui()

    def _start_flood(self, username: str) -> None:
        if self.statuses.get(username) != "CONNECTED":
            self.statusBar().showMessage(f"{username}: account is not connected", 2500)
            return
        presets = self._get_eligible_presets(username)
        if not presets:
            self.statusBar().showMessage(f"No enabled Flood messages are assigned to {username}", 3000)
            return

        self._stop_flood(username)
        self.account_notices.pop(username, None)
        self._refresh_accounts()
        stop_event = threading.Event()
        self.flood_stops[username] = stop_event
        settings = self.account_settings.setdefault(username, AccountSettings())
        settings.flood_running = True
        self.settings_store.save(self.account_settings)

        thread = threading.Thread(
            target=self._flood_worker,
            args=(username, stop_event),
            name=f"flood-{username}",
            daemon=True,
        )
        self.flood_threads[username] = thread
        thread.start()
        self.statusBar().showMessage(f"Flood started for {username}", 2000)

    def _stop_flood(self, username: str) -> None:
        stop_event = self.flood_stops.pop(username, None)
        if stop_event:
            stop_event.set()
        thread = self.flood_threads.pop(username, None)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.25)
        settings = self.account_settings.get(username)
        if settings:
            settings.flood_running = False
        self.settings_store.save(self.account_settings)
        if self.account_notices.pop(username, None) is not None and hasattr(self, "account_list"):
            self._refresh_accounts()

    def _flood_worker(self, username: str, stop_event: threading.Event) -> None:
        client = self.clients.get(username)
        if client is None:
            self.bridge.connection_error.emit(username, "Flood: no active Twitch connection")
            return

        warned_exhausted = False
        while not stop_event.is_set():
            if self.statuses.get(username) != "CONNECTED":
                stop_event.wait(1.0)
                continue

            presets = self._get_eligible_presets(username)
            if not presets:
                if not warned_exhausted:
                    self.bridge.connection_error.emit(username, "Flood: no eligible messages")
                    warned_exhausted = True
                stop_event.wait(30.0)
                continue

            now = time.time()
            eligible: list[PreparedMessage] = []
            wait_until: Optional[float] = None
            # Lock selection and reservation together so simultaneous account
            # workers cannot send the same preset at nearly the same time.
            with self.history_store._lock:
                shared_history = history.setdefault("__shared__", {})
                for preset in presets:
                    last_sent = float(shared_history.get(preset.name, 0.0))
                    repeat_after = max(DEFAULT_REPEAT_AFTER, preset.repeat_after)
                    if now - last_sent >= repeat_after:
                        eligible.append(preset)
                    else:
                        expires_at = last_sent + repeat_after
                        if wait_until is None or expires_at < wait_until:
                            wait_until = expires_at

                if eligible:
                    preset = random.choice(eligible)
                    reserved_at = time.time()
                    shared_history[preset.name] = reserved_at
                    self.history_store.path.write_text(
                        json.dumps(history, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

            if not eligible:
                wait_for = max(1.0, (wait_until - now) if wait_until is not None else 60.0)
                if not warned_exhausted:
                    self.bridge.connection_error.emit(
                        username,
                        f"Flood: all messages used; next message available in {self._format_duration(wait_for)}",
                    )
                    warned_exhausted = True
                stop_event.wait(min(wait_for, 60.0))
                continue

            if warned_exhausted:
                self.bridge.connection_status.emit(username, "FLOOD_READY")
                warned_exhausted = False
            ok = False
            # Serialize sends across all Flood accounts. A short shared gap
            # prevents simultaneous chat posts while keeping each account's
            # own (much longer) preset cooldown intact.
            while not stop_event.is_set():
                if self.flood_send_lock.acquire(timeout=0.2):
                    try:
                        remaining = (
                            self.last_flood_send_at
                            + FLOOD_ACCOUNT_GAP
                            - time.monotonic()
                        )
                        if remaining > 0 and stop_event.wait(remaining):
                            break
                        if stop_event.is_set():
                            break
                        try:
                            ok = client.send_message(preset.text)
                        except ValueError as exc:
                            self.bridge.connection_error.emit(username, f"Flood: {exc}")
                        if ok:
                            self.last_flood_send_at = time.monotonic()
                        break
                    finally:
                        self.flood_send_lock.release()

            if ok:
                self.bridge.log_message.emit(
                    f"[FLOOD SEND] {username}: {preset.name} → {preset.text} · next in {preset.cooldown:g}s"
                )
                if stop_event.wait(preset.cooldown):
                    break
            else:
                with self.history_store._lock:
                    shared_history = history.setdefault("__shared__", {})
                    if shared_history.get(preset.name) == reserved_at:
                        shared_history.pop(preset.name, None)
                        self.history_store.path.write_text(
                            json.dumps(history, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                self.bridge.log_message.emit(f"[FLOOD SEND FAILED] {username}: Twitch socket is not writable")
                stop_event.wait(1.0)

        self.bridge.connection_status.emit(username, "FLOOD_STOPPED")

    def _send_manual_message(self) -> None:
        username = self.selected_username
        if not username or self.account_settings.get(username, AccountSettings()).mode != "message":
            return
        client = self.clients.get(username)
        if client is None or self.statuses.get(username) != "CONNECTED":
            self.statusBar().showMessage("Selected account is not connected.", 2500)
            return
        text = self.message_edit.text().strip()
        if not text:
            return
        try:
            if client.send_message(text):
                self.pending_sent_messages[username.lower()] = (text, time.time())
                self._log(f"[SEND] {username}: {text} · written to Twitch socket")
                self.message_edit.clear()
                self.statusBar().showMessage(f"Sent as {username}", 1800)
            else:
                self._log(f"[SEND FAILED] {username}: Twitch socket is not writable")
        except ValueError as exc:
            QMessageBox.warning(self, "Message", str(exc))

    def _send_broadcast_message(self) -> None:
        text = self.broadcast_edit.text().strip()
        if not text:
            return
        connected = [
            (username, self.clients.get(username))
            for username in sorted(self.accounts)
            if self.statuses.get(username) == "CONNECTED"
            and self.clients.get(username) is not None
        ]
        if not connected:
            self.statusBar().showMessage("No connected accounts are available.", 2500)
            return
        if len(text) > MAX_MESSAGE_LENGTH:
            QMessageBox.warning(
                self,
                "Message",
                f"Message is longer than {MAX_MESSAGE_LENGTH} characters",
            )
            return

        interval = self.broadcast_interval.value() / 1000.0
        self.broadcast_edit.clear()
        self.broadcast_edit.setEnabled(False)
        self.broadcast_button.setEnabled(False)
        self.statusBar().showMessage(f"Sending from {len(connected)} connected accounts…", 2500)

        def worker() -> None:
            succeeded: list[str] = []
            failed: list[str] = []
            previous_send = 0.0
            for username, client in connected:
                if client is None:
                    failed.append(username)
                    continue
                if previous_send:
                    remaining = interval - (time.monotonic() - previous_send)
                    if remaining > 0:
                        time.sleep(remaining)
                try:
                    if client.send_message(text):
                        succeeded.append(username)
                        previous_send = time.monotonic()
                        self.bridge.log_message.emit(
                            f"[BROADCAST SEND] {username}: {text}"
                        )
                    else:
                        failed.append(username)
                        self.bridge.log_message.emit(
                            f"[BROADCAST SEND FAILED] {username}: Twitch socket is not writable"
                        )
                except ValueError as exc:
                    failed.append(username)
                    self.bridge.log_message.emit(f"[BROADCAST SEND FAILED] {username}: {exc}")
            summary = f"Sent from {len(succeeded)}/{len(connected)} accounts"
            if failed:
                summary += f"; failed: {', '.join(failed)}"
            self.bridge.broadcast_finished.emit(summary)

        threading.Thread(target=worker, name="broadcast-send", daemon=True).start()

    def _broadcast_finished(self, summary: str) -> None:
        self.broadcast_edit.setEnabled(True)
        self.broadcast_button.setEnabled(True)
        self.statusBar().showMessage(summary, 5000)

    def _make_account_widget(self, username: str, status: str) -> QWidget:
        widget = QWidget()
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, max(5, round(5 * self.scale)), 8, max(5, round(5 * self.scale)))
        layout.setSpacing(max(6, round(7 * self.scale)))
        widget.setMinimumHeight(self._text_row_height(11))

        name = QLabel(username)
        name.setObjectName("accountName")
        name.setStyleSheet(
            f"font-weight: {'750' if username == self.selected_username else '650'}; "
            f"color: {self._account_name_color(username) if username == self.selected_username else '#e6e9ef'}; "
            "background: transparent;"
        )
        layout.addWidget(name, 1)

        working = status == "CONNECTED"
        notice = self.account_notices.get(username)
        if notice:
            status_text = f"⚠ {notice}"
        elif working:
            status_text = "● CONNECTED · WORKING"
        elif status == "QUEUED":
            status_text = "● QUEUED · WAIT"
        elif status in {"CHECKING", "CONNECTING", "RECONNECTING"}:
            status_text = "● CHECKING · WAIT"
        else:
            status_text = "● OFFLINE · NOT WORKING"

        status_label = QLabel(status_text)
        status_color = QColor("#e3b341") if notice else self._status_color(status)
        status_label.setStyleSheet(
            f"font-size: {max(9, round(11*self.scale))}px; font-weight: 700; color: {status_color.name()};"
        )
        layout.addWidget(status_label)
        return widget

    def _refresh_accounts(self) -> None:
        current = self.selected_username
        self.account_list.blockSignals(True)
        self.account_list.clear()
        for username in sorted(self.accounts):
            status = self.statuses.get(username, "OFFLINE")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, username)
            account_widget = self._make_account_widget(username, status)
            self.account_list.addItem(item)
            self.account_list.setItemWidget(item, account_widget)
            # Size the item from the actual embedded widget, not from an
            # approximation. This keeps ascenders/descenders fully visible.
            item.setSizeHint(self._account_size_hint(account_widget))
        self.account_list.blockSignals(False)
        if current and current in self.accounts:
            self._select_username(current)
        elif self.account_list.count():
            self.account_list.setCurrentRow(0)
        else:
            self.selected_username = None
            self._show_account_settings()
        self._update_status_label()
        self._update_account_selection_visuals()
        has_accounts = self.account_list.count() > 0
        if hasattr(self, "account_up_button"):
            self.account_up_button.setEnabled(has_accounts)
        if hasattr(self, "account_down_button"):
            self.account_down_button.setEnabled(has_accounts)

    def _account_size_hint(self, widget: QWidget | None = None) -> QSize:
        if widget is not None:
            hint = widget.sizeHint()
            # QListWidget can shave a pixel or two from an embedded widget.
            # Keep enough vertical room for ascent + descent of the largest font.
            return QSize(-1, max(hint.height() + 4, self._text_row_height(11)))

        return QSize(-1, self._text_row_height(11))

    def _text_row_height(self, point_size: int) -> int:
        font = QFont("Sans", max(9, round(point_size * self.scale)))
        fm = QFontMetrics(font)
        # Use ascent + descent instead of lineSpacing: this prevents Qt's
        # glyph clipping when the font has a tight line box.
        glyph_height = fm.ascent() + fm.descent()
        return max(glyph_height + max(10, round(12 * self.scale)), fm.lineSpacing() + max(8, round(10 * self.scale)))

    @staticmethod
    def _status_color(status: str) -> QColor:
        if status == "CONNECTED":
            return QColor("#55d187")
        if status in {"CHECKING", "CONNECTING", "RECONNECTING", "QUEUED"}:
            return QColor("#e3b341")
        return QColor("#e56868")

    def _update_status_label(self) -> None:
        # Status is displayed directly in the account list.
        return

    def _select_username(self, username: str) -> None:
        for row in range(self.account_list.count()):
            item = self.account_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == username:
                self.account_list.setCurrentRow(row)
                break

    def _update_connect_button(self) -> None:
        self.connect_button.setEnabled(bool(self.channel_edit.text().strip().lstrip("#")))

    def _add_account(self) -> None:
        dialog = AddAccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        username, token = dialog.values()
        if username in self.accounts:
            QMessageBox.warning(self, "Already exists", f"Account '{username}' is already added.")
            return
        self.accounts[username] = Account(username=username, token=token)
        self.account_settings.setdefault(username, AccountSettings())
        self.account_store.save(list(self.accounts.values()))
        self.settings_store.save(self.account_settings)
        self._refresh_accounts()
        self._select_username(username)
        if self.current_channel:
            self._connect_account(self.accounts[username])

    def _remove_account(self) -> None:
        username = self.selected_username
        if not username or username not in self.accounts:
            return
        if QMessageBox.question(self, "Remove account", f"Remove '{username}' from this application?") != QMessageBox.StandardButton.Yes:
            return
        if any(account.username == username for account in self.connection_queue):
            self.connection_queue = [account for account in self.connection_queue if account.username != username]
            if not self.connection_queue and self.connection_timer.isActive():
                self.connection_timer.stop()
        self._stop_flood(username)
        client = self.clients.pop(username, None)
        if client:
            client.stop()
        self.accounts.pop(username, None)
        self.account_settings.pop(username, None)
        self.statuses.pop(username, None)
        self.history_store.remove_account(username)
        self.account_store.save(list(self.accounts.values()))
        self.settings_store.save(self.account_settings)
        self.selected_username = None
        self._refresh_accounts()

    def _cancel_connection_sequence(self, *, clear_queue: bool = True) -> None:
        if self.connection_timer.isActive():
            self.connection_timer.stop()
        if clear_queue and self.connection_queue:
            self._log(f"[QUEUE] Cancelled: {len(self.connection_queue)} account(s) were waiting")
        if clear_queue:
            self.connection_queue.clear()

    def _connect_all(self) -> None:
        channel = self.channel_edit.text().strip().lstrip("#").lower()
        if not channel:
            QMessageBox.warning(self, "Channel", "Enter a Twitch channel name first.")
            self.channel_edit.setFocus()
            return

        self._cancel_connection_sequence()
        self.current_channel = channel
        self.chat_header.setText("#" + channel)
        self._log(f"[CHANNEL] Connecting to #{channel}")

        for username in list(self.flood_stops):
            self._stop_flood(username)

        for username, client in list(self.clients.items()):
            self._log(f"[DISCONNECT] {username} → #{self.current_channel}")
            client.stop()

        self.clients.clear()
        self.statuses.clear()
        self.recent_message_ids.clear()
        self.recent_message_order.clear()
        self.chat_list.clear()

        self.connection_queue = list(self.accounts.values())
        if not self.connection_queue:
            return

        for account in self.connection_queue:
            self.statuses[account.username] = "QUEUED"

        self._refresh_accounts()
        self._log(
            f"[QUEUE] {len(self.connection_queue)} account(s) queued for #{self.current_channel}"
        )
        self.statusBar().showMessage(
            f"Queued {len(self.connection_queue)} accounts for sequential connection",
            2500,
        )
        self._schedule_next_queued_account()

    def _schedule_next_queued_account(self) -> None:
        if not self.connection_queue or not self.current_channel:
            return

        account = self.connection_queue[0]
        settings = self.account_settings.setdefault(account.username, AccountSettings())
        delay = max(0.0, float(settings.connect_delay))
        self.statuses[account.username] = "QUEUED"
        self._refresh_accounts()
        self._log(
            f"[QUEUE] {account.username}: waiting {delay:g}s before connecting to #{self.current_channel}"
        )
        self.connection_timer.start(max(0, int(round(delay * 1000))))

    def _connect_next_queued_account(self) -> None:
        if not self.connection_queue or not self.current_channel:
            return

        account = self.connection_queue.pop(0)
        if account.username not in self.accounts:
            self._schedule_next_queued_account()
            return

        self._log(
            f"[QUEUE] {account.username}: delay complete, starting connection to #{self.current_channel}"
        )
        self._connect_account(account)

        if self.connection_queue:
            self._schedule_next_queued_account()
        else:
            self._log("[QUEUE] All accounts have been started")

    def _refresh_selected_or_all(self) -> None:
        if self.selected_username and self.selected_username in self.accounts:
            if not self.current_channel:
                self.channel_edit.setFocus()
                self.statusBar().showMessage("Enter a channel first.", 2000)
                return
            self._cancel_connection_sequence()
            self._log(f"[RECONNECT REQUEST] {self.selected_username} → #{self.current_channel}")
            self._connect_account(self.accounts[self.selected_username])
            self.statusBar().showMessage(f"Rechecking {self.selected_username}...", 1500)
        elif self.current_channel:
            self._connect_all()
        else:
            self.channel_edit.setFocus()
            self.statusBar().showMessage("Enter a channel first.", 2000)

    def _connect_account(self, account: Account) -> None:
        if not self.current_channel:
            return
        existing = self.clients.get(account.username)
        if existing:
            self._stop_flood(account.username)
            self._log(f"[DISCONNECT] {account.username} → replacing existing connection")
            existing.stop()
        self._log(f"[CONNECT] {account.username} → #{self.current_channel}")
        client_ref: dict[str, TwitchIRCClient] = {}
        is_current = lambda: self.clients.get(account.username) is client_ref.get("client")
        client = TwitchIRCClient(
            account,
            self.current_channel,
            lambda *args: self._message_callback(*args) if is_current() else None,
            lambda username, status: self._status_callback(username, status) if is_current() else None,
            lambda username, error: self._error_callback(username, error) if is_current() else None,
            lambda username, direction, line: self._wire_callback(username, direction, line) if is_current() else None,
        )
        client_ref["client"] = client
        self.clients[account.username] = client
        self.statuses[account.username] = "CHECKING"
        self._refresh_accounts()
        client.start()

    def _message_callback(self, username: str, message: str, channel: str, message_id: str, color: str) -> None:
        self.bridge.message_received.emit(username, message, channel, message_id, color)

    def _status_callback(self, username: str, status: str) -> None:
        self.bridge.connection_status.emit(username, status)

    def _error_callback(self, username: str, error: str) -> None:
        self.bridge.connection_error.emit(username, error)

    def _wire_callback(self, username: str, direction: str, line: str) -> None:
        self.bridge.log_message.emit(
            f"{time.strftime('%H:%M:%S')} [IRC {direction}] {username}: {line}"
        )

    def _handle_status(self, username: str, status: str) -> None:
        if username not in self.accounts:
            return
        if status == "FLOOD_READY":
            self.account_notices.pop(username, None)
            self._refresh_accounts()
            return
        if status == "FLOOD_STOPPED":
            settings = self.account_settings.get(username)
            if settings:
                settings.flood_running = False
                self.settings_store.save(self.account_settings)
            if username == self.selected_username:
                self._update_mode_ui()
            self.statusBar().showMessage(f"{username}: Flood stopped", 1800)
            return

        self.statuses[username] = status
        self._log(f"[STATUS] {username}: {status}")
        self._refresh_accounts()
        if username == self.selected_username:
            self._update_mode_ui()
        self.statusBar().showMessage(f"{username}: {status}", 2200)

    def _handle_error(self, username: str, error: str) -> None:
        if username not in self.accounts:
            return
        if error.startswith("Flood:"):
            self.account_notices[username] = (
                "NO MESSAGES" if "no eligible messages" in error else "MESSAGES USED"
            )
            self._refresh_accounts()
        self._log(f"[ERROR] {username}: {error}")
        self.statusBar().showMessage(f"{username}: {error}", 5000)

    def _log(self, message: str) -> None:
        self.bridge.log_message.emit(f"{time.strftime('%H:%M:%S')} {message}")

    def _append_log(self, message: str) -> None:
        self.log_history.append(message)
        if len(self.log_history) > 5000:
            del self.log_history[:-5000]

        if self.logs_view is None:
            return
        cursor = self.logs_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(message + "\n")
        self.logs_view.setTextCursor(cursor)
        self.logs_view.ensureCursorVisible()

    def _create_logs_dialog(self) -> None:
        if self.logs_dialog is not None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Twitch Multi Chat — IRC Log")
        dialog.resize(900, 560)
        layout = QVBoxLayout(dialog)

        self.logs_view = QTextBrowser(dialog)
        self.logs_view.setReadOnly(True)
        self.logs_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.logs_view.setFont(QFont("Monospace", 10))
        layout.addWidget(self.logs_view, 1)
        if self.log_history:
            self.logs_view.setPlainText("\n".join(self.log_history))
            cursor = self.logs_view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.logs_view.setTextCursor(cursor)

        controls = QHBoxLayout()
        controls.addStretch(1)
        clear_button = QPushButton("Clear")
        def clear_logs() -> None:
            self.log_history.clear()
            if self.logs_view:
                self.logs_view.clear()
        clear_button.clicked.connect(clear_logs)
        controls.addWidget(clear_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.hide)
        controls.addWidget(close_button)
        layout.addLayout(controls)

        self.logs_dialog = dialog

    def _toggle_logs(self) -> None:
        if self.logs_dialog is None:
            self._create_logs_dialog()
        assert self.logs_dialog is not None
        if self.logs_dialog.isVisible():
            self.logs_dialog.hide()
        else:
            self.logs_dialog.show()
            self.logs_dialog.raise_()
            self.logs_dialog.activateWindow()

    def _fallback_name_color(self, username: str) -> str:
        index = sum((i + 1) * ord(char) for i, char in enumerate(username.lower())) % len(self.ACCOUNT_COLORS)
        return self.ACCOUNT_COLORS[index]


    def _append_chat_message(self, username: str, message: str, color: str) -> None:
        """Append one chat message and keep the view pinned to the bottom.

        If the user is already at the bottom, new messages automatically scroll
        the chat down like Twitch. If the user scrolls up, their position is kept.
        """
        font_size = max(10.5, 12.0 * self.scale)
        safe_color = (
            color
            if color and color.startswith("#") and len(color) in {4, 7}
            else self._fallback_name_color(username)
        )

        scrollbar = self.chat_list.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 12

        cursor = self.chat_list.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # Create a completely new block for every message.
        block_format = QTextBlockFormat()
        block_format.setBackground(QColor("#171a20"))
        block_format.setTopMargin(2)
        block_format.setBottomMargin(2)
        block_format.setLeftMargin(7)
        block_format.setRightMargin(7)

        cursor.insertBlock(block_format)

        base_format = QTextCharFormat()
        base_format.setFont(self.chat_list.font())
        base_format.setFontPointSize(font_size)

        name_format = QTextCharFormat(base_format)
        name_format.setForeground(QColor(safe_color))
        name_format.setFontWeight(QFont.Weight.DemiBold)

        separator_format = QTextCharFormat(base_format)
        separator_format.setForeground(QColor("#707887"))

        message_format = QTextCharFormat(base_format)
        message_format.setForeground(QColor("#e6e9ef"))

        clean_message = message.replace("\r", " ").replace("\n", " ")

        cursor.insertText(username, name_format)
        cursor.insertText(": ", separator_format)
        cursor.insertText(clean_message, message_format)

        if was_at_bottom:
            # Let QTextDocument finish recalculating the layout first.
            QTimer.singleShot(
                0,
                lambda: self._scroll_chat_to_bottom(),
            )

    def _scroll_chat_to_bottom(self) -> None:
        scrollbar = self.chat_list.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.chat_list.moveCursor(QTextCursor.MoveOperation.End)
        self.chat_list.ensureCursorVisible()

    def _handle_message(
        self,
        username: str,
        message: str,
        channel: str,
        message_id: str,
        color: str,
    ) -> None:
        if not self.current_channel or channel != self.current_channel:
            return

        if message_id:
            if message_id in self.recent_message_ids:
                return

            self.recent_message_ids.add(message_id)
            self.recent_message_order.append(message_id)

            if len(self.recent_message_order) > 5000:
                old = self.recent_message_order.pop(0)
                self.recent_message_ids.discard(old)

        self._append_chat_message(username, message, color)

        pending = self.pending_sent_messages.get(username.lower())
        if pending and pending[0] == message:
            self.pending_sent_messages.pop(username.lower(), None)
            self._log(f"[ECHO] {username}: message received back from Twitch")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == event.Type.KeyPress and event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Down}:
            focus = QApplication.focusWidget()
            if not isinstance(focus, QComboBox):
                delta = -1 if event.key() == Qt.Key.Key_Up else 1
                if self.account_list.count() > 0:
                    self._move_account_selection(delta)
                    return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:
        self._log("[APP] Shutting down")
        self._cancel_connection_sequence()
        for username in list(self.flood_stops):
            self._stop_flood(username)
        for client in list(self.clients.values()):
            client.stop()
        self.clients.clear()
        event.accept()
