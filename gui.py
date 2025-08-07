import sys
import json
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QProgressBar, QTextEdit, QHBoxLayout, QVBoxLayout, QFileDialog, QTableWidget, QTableWidgetItem, QSizePolicy, QMenuBar, QMenu, QDialog, QFormLayout, QSpinBox, QCheckBox, QDialogButtonBox, QHeaderView
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Qt, QThread, Signal

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from script import run_downloader

class DownloadThread(QThread):
    status_update = Signal(str)
    progress_update = Signal(int, int)
    table_update = Signal(object)
    worker_update = Signal(object)

    def __init__(self, csv_file, config):
        super().__init__()
        self.csv_file = csv_file
        self.config = config
        self._should_stop = False

    def stop(self):
        self._should_stop = True

    def run(self):
        def status_cb(msg):
            self.status_update.emit(msg)
        def progress_cb(completed, total):
            self.progress_update.emit(completed, total)
        def results_cb(counts):
            self.table_update.emit(counts.copy())
        def workers_cb(workers):
            self.worker_update.emit(list(workers.items()))  # Transmit as list of tuples
        # Call backend downloader, pass stop flag
        run_downloader(
            self.csv_file,
            status_callback=status_cb,
            progress_callback=progress_cb,
            config=self.config,
            results_callback=results_cb,
            worker_callback=workers_cb,
            should_stop=lambda: self._should_stop
        )

class SettingsDialog(QDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.config = config or {}
        layout = QFormLayout(self)
        self.max_workers = QSpinBox()
        self.max_workers.setRange(1, 100)
        self.max_workers.setValue(self.config.get("MAX_WORKERS", 5))
        layout.addRow("Max Workers", self.max_workers)
        self.max_api_workers = QSpinBox()
        self.max_api_workers.setRange(1, 10)
        self.max_api_workers.setValue(self.config.get("MAX_API_WORKERS", 1))
        layout.addRow("Max API Workers", self.max_api_workers)
        self.requests_per_minute = QSpinBox()
        self.requests_per_minute.setRange(1, 1000)
        self.requests_per_minute.setValue(self.config.get("REQUESTS_PER_MINUTE", 120))
        layout.addRow("Requests per Minute", self.requests_per_minute)
        self.max_attempts = QSpinBox()
        self.max_attempts.setRange(1, 10)
        self.max_attempts.setValue(self.config.get("MAX_ATTEMPTS", 3))
        layout.addRow("Max Attempts", self.max_attempts)
        self.logging = QCheckBox("Enable Logging")
        self.logging.setChecked(self.config.get("LOGGING", True))
        layout.addRow(self.logging)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    def get_settings(self):
        return {
            "MAX_WORKERS": self.max_workers.value(),
            "MAX_API_WORKERS": self.max_api_workers.value(),
            "REQUESTS_PER_MINUTE": self.requests_per_minute.value(),
            "MAX_ATTEMPTS": self.max_attempts.value(),
            "LOGGING": self.logging.isChecked(),
        }

class DatasheetGrabberGUI(QWidget):
    def update_progress(self, completed, total):
        self.progress.setMaximum(total)
        self.progress.setValue(completed)

    def update_results_table(self, counts):
        # Always set result labels in column 0
        results = [
            "✅ Downloaded",
            "⏭️ Skipped",
            "⚠️ No datasheet",
            "❌ Not found",
            "⚠️ Download failed",
            "❌ Errors"
        ]
        for i in range(self.results_table.rowCount()):
            self.results_table.setItem(i, 0, QTableWidgetItem(results[i]))
            count = counts.get(i, 0)
            self.results_table.setItem(i, 1, QTableWidgetItem(str(count)))
        self.results_table.repaint()

    def update_worker_table(self, workers_list):
        # Reconstruct dict from list of tuples
        workers = dict(workers_list)
        print("[DEBUG] update_worker_table received:", workers)
        num_api = self.config.get("MAX_API_WORKERS", 1)
        num_dl = self.config.get("MAX_WORKERS", 5)
        total = num_api + num_dl
        for i in range(total):
            if i < num_api:
                label = f"API-Worker-{i+1}"
            else:
                label = f"DL-Worker-{i+1-num_api}"
            status = workers.get(i, "Idle")
            print(f"[DEBUG] Setting row {i}: {label} -> {status}")
            self.worker_table.setItem(i, 0, QTableWidgetItem(label))
            self.worker_table.setItem(i, 1, QTableWidgetItem(str(status)))
    def append_status(self, msg):
        # You may want to add a status text area for messages
        pass  # For now, do nothing
    def start_download(self):
        # If thread is running, treat as stop
        thread = getattr(self, 'thread', None)
        from PySide6.QtCore import QThread
        if thread is not None and isinstance(thread, QThread) and thread.isRunning():
            self.stop_download()
            return
        csv_file = self.file_entry.text()
        if not csv_file:
            return
        self.progress.setValue(0)
        self.thread = DownloadThread(csv_file, self.config)
        self.thread.status_update.connect(self.append_status)
        self.thread.progress_update.connect(self.update_progress)
        self.thread.table_update.connect(self.update_results_table)
        self.thread.worker_update.connect(self.update_worker_table)
        self.thread.finished.connect(self.download_finished)
        self.start_button.setText("Stop")
        self.thread.start()

    def stop_download(self):
        thread = getattr(self, 'thread', None)
        from PySide6.QtCore import QThread
        if thread is not None and isinstance(thread, QThread) and thread.isRunning():
            thread.stop()  # Set stop flag for backend
            thread.quit()
        self.start_button.setText("Start Download")

    def download_finished(self):
        self.start_button.setText("Start Download")
    def open_settings(self):
        dlg = SettingsDialog(self, self.config)
        if dlg.exec():
            self.config = dlg.get_settings()
    def browse_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Select CSV File", "", "CSV Files (*.csv)")
        if filename:
            self.file_entry.setText(filename)
    def create_menu(self):
        menubar = QMenuBar(self)
        return menubar

    def open_api_keys_dialog(self):
        dlg = APIKeysDialog(self)
        dlg.exec()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Datasheet Grabber")
        self.setGeometry(100, 100, 700, 500)
        self.config = {
            "MAX_WORKERS": 5,
            "MAX_API_WORKERS": 1,
            "REQUESTS_PER_MINUTE": 120,
            "MAX_ATTEMPTS": 3,
            "LOGGING": True,
        }
        self.init_ui()

    def init_ui(self):
        menubar = self.create_menu()
        # File selector row
        file_label = QLabel("Parts CSV File:")
        self.file_entry = QLineEdit()
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_file)
        file_row = QHBoxLayout()
        file_row.addWidget(file_label)
        file_row.addWidget(self.file_entry, stretch=1)
        file_row.addWidget(browse_button)

    # Settings and API Keys icon buttons (top right)
        settings_btn = QPushButton()
        settings_btn.setText("⚙️")  # Unicode cog icon
        settings_btn.setToolTip("Settings")
        settings_btn.setFixedSize(32, 32)
        settings_btn.clicked.connect(self.open_settings)

        api_btn = QPushButton()
        api_btn.setText("🔑")  # Unicode key icon
        api_btn.setToolTip("API Keys")
        api_btn.setFixedSize(32, 32)
        api_btn.clicked.connect(self.open_api_keys_dialog)

        settings_row = QHBoxLayout()
        settings_row.addStretch(1)
        settings_row.addWidget(api_btn)
        settings_row.addWidget(settings_btn)

        # Start button
        self.start_button = QPushButton("Start Download")
        self.start_button.clicked.connect(self.start_download)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Results table
        self.results_table = QTableWidget()
        self.results_table.setRowCount(6)
        self.results_table.setColumnCount(2)
        self.results_table.setHorizontalHeaderLabels(["Result", "Count"])
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setShowGrid(False)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results_table.setStyleSheet("QTableWidget { border: none; }")
        self.results_table.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.results_table.setMinimumWidth(220)
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        results = [
            "✅ Downloaded",
            "⏭️ Skipped",
            "⚠️ No datasheet",
            "❌ Not found",
            "⚠️ Download failed",
            "❌ Errors"
        ]
        for i, result in enumerate(results):
            self.results_table.setItem(i, 0, QTableWidgetItem(result))
            self.results_table.setItem(i, 1, QTableWidgetItem("0"))

        # Worker table
        num_api = self.config.get("MAX_API_WORKERS", 1)
        num_dl = self.config.get("MAX_WORKERS", 5)
        total = num_api + num_dl
        self.worker_table = QTableWidget()
        self.worker_table.setRowCount(total)
        self.worker_table.setColumnCount(2)
        self.worker_table.setHorizontalHeaderLabels(["Worker ID", "Status"])
        self.worker_table.verticalHeader().setVisible(False)
        self.worker_table.horizontalHeader().setVisible(False)
        self.worker_table.setShowGrid(False)
        self.worker_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.worker_table.setStyleSheet("QTableWidget { border: none; }")
        self.worker_table.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.worker_table.setMinimumWidth(220)
        self.worker_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.worker_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for i in range(total):
            if i < num_api:
                label = f"API-Worker-{i+1}"
            else:
                label = f"DL-Worker-{i+1-num_api}"
            self.worker_table.setItem(i, 0, QTableWidgetItem(label))
            self.worker_table.setItem(i, 1, QTableWidgetItem("Idle"))

        # Tables layout (results left, workers right)
        tables_row = QHBoxLayout()
        tables_row.addWidget(self.results_table, stretch=1)
        tables_row.addWidget(self.worker_table, stretch=1)

        # Layout
        layout = QVBoxLayout()
        layout.setMenuBar(menubar)
        layout.addLayout(settings_row)
        layout.addLayout(file_row)
        layout.addWidget(self.start_button)
        layout.addWidget(self.progress)
        layout.addLayout(tables_row, stretch=1)
        self.setLayout(layout)

class APIKeysDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit API Keys")
        self.resize(400, 180)
        layout = QFormLayout(self)

        # Load current values
        client_id = ""
        client_secret = ""
        try:
            with open("api_keys.json", "r") as f:
                data = json.load(f)
                keys = data.get("api_keys", [{}])[0]
                client_id = keys.get("CLIENT_ID", "")
                client_secret = keys.get("CLIENT_SECRET", "")
        except Exception:
            pass

        self.client_id_edit = QLineEdit(client_id)
        self.client_secret_edit = QLineEdit(client_secret)
        self.client_secret_edit.setEchoMode(QLineEdit.Password)
        layout.addRow("Client ID:", self.client_id_edit)
        layout.addRow("Client Secret:", self.client_secret_edit)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addRow(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save_keys)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save_keys(self):
        client_id = self.client_id_edit.text().strip()
        client_secret = self.client_secret_edit.text().strip()
        if not client_id or not client_secret:
            self.error_label.setText("Both fields are required.")
            return
        try:
            data = {"api_keys": [{"CLIENT_ID": client_id, "CLIENT_SECRET": client_secret}]}
            with open("api_keys.json", "w") as f:
                json.dump(data, f, indent=2)
            self.accept()
        except Exception as e:
            self.error_label.setText(f"Failed to save: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DatasheetGrabberGUI()
    # Ensure window is not minimized and is active
    window.setWindowState(window.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
    window.activateWindow()
    # Optionally center the window
    screen = app.primaryScreen()
    if screen:
        geo = screen.availableGeometry()
        center = geo.center()
        frame = window.frameGeometry()
        frame.moveCenter(center)
        window.move(frame.topLeft())
    window.show()
    sys.exit(app.exec())
