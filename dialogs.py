from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)


class AddAccountDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Twitch account")
        self.resize(450, 190)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("twitch_username")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("oauth:... or raw token")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username", self.username_edit)
        form.addRow("OAuth token", self.token_edit)
        layout.addLayout(form)

        hint = QLabel("Token must belong to this Twitch account and include chat:read + chat:edit.")
        hint.setWordWrap(True)
        hint.setObjectName("secondary")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.username_edit.text().strip() or not self.token_edit.text().strip():
            QMessageBox.warning(self, "Missing data", "Enter both username and OAuth token.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.username_edit.text().strip().lower(), self.token_edit.text().strip()
