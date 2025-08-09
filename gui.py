import sys
import json
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QProgressBar, QHBoxLayout,
    QVBoxLayout, QFileDialog, QTableWidget, QTableWidgetItem, QDialog, QFormLayout, 
    QSpinBox, QCheckBox, QDialogButtonBox, QHeaderView, QTextEdit, QSplitter
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon, QFont
from script import run_downloader

SETTINGS_FILE = "settings.json"

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
            self.worker_update.emit(dict(workers))
        
        try:
            run_downloader(
                self.csv_file,
                status_callback=status_cb,
                progress_callback=progress_cb,
                config=self.config,
                results_callback=results_cb,
                worker_callback=workers_cb,
                should_stop=lambda: self._should_stop
            )
        except Exception as e:
            self.status_update.emit(f"❌ Error: {str(e)}\n")

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
    def __init__(self):
        super().__init__()
        self.thread = None
        self.setWindowIcon(QIcon("app.ico"))
        self.setWindowTitle("Datasheet Grabber")
        self.setGeometry(100, 100, 900, 600)
        self.config = self.load_settings()
        
        # Performance optimizations
        self.status_buffer = []
        self.last_worker_update = {}
        self.last_results_update = {}
        
        # Timers for batched updates
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.flush_status_buffer)
        self.status_timer.setSingleShot(True)
        
        self.worker_timer = QTimer()
        self.worker_timer.timeout.connect(self.flush_worker_updates)
        self.worker_timer.setSingleShot(True)
        
        self.init_ui()
    
    def load_settings(self):
        default = {
            "MAX_WORKERS": 5,
            "MAX_API_WORKERS": 1,
            "REQUESTS_PER_MINUTE": 120,
            "MAX_ATTEMPTS": 3,
            "LOGGING": True,
        }
        try:
            with open(SETTINGS_FILE, "r") as f:
                cfg = json.load(f)
                default.update(cfg)
        except Exception:
            pass
        return default
    
    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass
    
    def init_ui(self):
        # Main layout
        main_layout = QVBoxLayout()
        
        # File selection row
        file_label = QLabel("Parts CSV File:")
        self.file_entry = QLineEdit()
        self.file_entry.setFixedHeight(30)
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_file)
        browse_button.setFixedWidth(70)
        browse_button.setFixedHeight(32) 
        
        file_row = QHBoxLayout()
        file_row.addWidget(file_label)
        file_row.addWidget(self.file_entry, stretch=1)
        file_row.addWidget(browse_button)
        
        # Control buttons row (Start Download takes most space + Settings/API buttons on right)
        controls_row = QHBoxLayout()
        controls_row.setSpacing(5)  # Set spacing between elements
        
        self.start_button = QPushButton("Start Download")
        self.start_button.clicked.connect(self.start_download)
        self.start_button.setMinimumHeight(32)
        
        # Settings buttons
        api_btn = QPushButton("🔑")
        api_btn.setToolTip("API Keys")
        api_btn.setFixedSize(32, 32)
        api_btn.clicked.connect(self.open_api_keys_dialog)
        
        settings_btn = QPushButton("⚙️")
        settings_btn.setToolTip("Settings")
        settings_btn.setFixedSize(32, 32)
        settings_btn.clicked.connect(self.open_settings)
        
        # Layout: Start button takes most space, small buttons on right
        controls_row.addWidget(self.start_button, stretch=1)  # stretch=1 makes it expand
        controls_row.addWidget(api_btn)      # Fixed size, no stretch
        controls_row.addWidget(settings_btn) # Fixed size, no stretch
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        
        # Create splitter for resizable layout
        splitter = QSplitter(Qt.Horizontal)
        
        # Left side - Results and Workers
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 5, 0)
        
        # Results table
        results_label = QLabel("Results:")
        results_label.setFont(QFont("Arial", 10, QFont.Bold))
        
        self.results_table = QTableWidget()
        self.setup_results_table()
        
        # Worker table
        worker_label = QLabel("Workers:")
        worker_label.setFont(QFont("Arial", 10, QFont.Bold))
        
        self.worker_table = QTableWidget()
        self.setup_worker_table()
        
        left_layout.addWidget(results_label)
        left_layout.addWidget(self.results_table)
        left_layout.addWidget(worker_label)
        left_layout.addWidget(self.worker_table, stretch=1)
        
        # Right side - Status log
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 0, 0, 0)
        
        status_label = QLabel("Status Log:")
        status_label.setFont(QFont("Arial", 10, QFont.Bold))
        
        self.status_log = QTextEdit()
        self.status_log.setFont(QFont("Consolas", 9))
        self.status_log.setReadOnly(True)
        # Optimize text widget
        self.status_log.document().setMaximumBlockCount(1000)  # Limit lines
        self.status_log.setLineWrapMode(QTextEdit.NoWrap)
        
        right_layout.addWidget(status_label)
        right_layout.addWidget(self.status_log)
        
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([350, 550])
        
        # Add everything to main layout
        main_layout.addLayout(file_row)
        main_layout.addLayout(controls_row)
        main_layout.addWidget(self.progress)
        main_layout.addWidget(splitter, stretch=1)
        
        self.setLayout(main_layout)
    
    def setup_results_table(self):
        self.results_table.setRowCount(6)
        self.results_table.setColumnCount(2)
        self.results_table.setHorizontalHeaderLabels(["Result", "Count"])
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results_table.setFixedHeight(206)
        self.results_table.setAlternatingRowColors(True)
        
        # Disable sorting for performance
        self.results_table.setSortingEnabled(False)
        
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
        
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    
    def setup_worker_table(self):
        self.refresh_worker_table()
        self.worker_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.worker_table.setAlternatingRowColors(True)
        self.worker_table.setSortingEnabled(False)  # Disable sorting for performance
        self.worker_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.worker_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    
    def refresh_worker_table(self):
        """Refresh worker table based on current config"""
        num_api = self.config.get("MAX_API_WORKERS", 1)
        num_dl = self.config.get("MAX_WORKERS", 5)
        total = num_api + num_dl
        
        self.worker_table.setRowCount(total)
        self.worker_table.setColumnCount(2)
        self.worker_table.setHorizontalHeaderLabels(["Worker", "Status"])
        self.worker_table.verticalHeader().setVisible(False)
        
        for i in range(total):
            if i < num_api:
                label = f"API-{i+1}"
            else:
                label = f"DL-{i+1-num_api}"
            
            self.worker_table.setItem(i, 0, QTableWidgetItem(label))
            self.worker_table.setItem(i, 1, QTableWidgetItem("Idle"))
    
    def open_api_keys_dialog(self):
        dlg = APIKeysDialog(self)
        dlg.exec()
    
    def open_settings(self):
        dlg = SettingsDialog(self, self.config)
        if dlg.exec():
            old_config = self.config.copy()
            self.config = dlg.get_settings()
            self.save_settings()
            
            # Refresh worker table if worker counts changed
            if (old_config.get("MAX_WORKERS") != self.config.get("MAX_WORKERS") or 
                old_config.get("MAX_API_WORKERS") != self.config.get("MAX_API_WORKERS")):
                self.refresh_worker_table()
    
    def browse_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select CSV File", "", "CSV Files (*.csv)"
        )
        if filename:
            self.file_entry.setText(filename)
    
    def start_download(self):
        if self.thread and self.thread.isRunning():
            self.stop_download()
            return
        
        csv_file = self.file_entry.text().strip()
        if not csv_file or not os.path.exists(csv_file):
            self.update_status("❌ Please select a valid CSV file\n")
            return
        
        # Clear previous results
        self.progress.setValue(0)
        self.status_log.clear()
        self.status_buffer.clear()
        self.last_worker_update.clear()
        self.last_results_update.clear()
        
        for i in range(self.results_table.rowCount()):
            self.results_table.setItem(i, 1, QTableWidgetItem("0"))
        
        # Start download thread
        self.thread = DownloadThread(csv_file, self.config)
        self.thread.status_update.connect(self.buffer_status_update)
        self.thread.progress_update.connect(self.update_progress)
        self.thread.table_update.connect(self.update_results_table)
        self.thread.worker_update.connect(self.buffer_worker_update)
        self.thread.finished.connect(self.download_finished)
        
        self.start_button.setText("Stop Download")
        self.start_button.setStyleSheet("QPushButton:hover { background-color: #ff4444; }")
        self.thread.start()
    
    def stop_download(self):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.buffer_status_update("⏹️ Stopping download...\n")
            self.thread.quit()
            self.thread.wait(3000)
    
    def download_finished(self):
        self.start_button.setText("Start Download")
        self.start_button.setStyleSheet("")
        self.buffer_status_update("✅ Download process finished\n")
        # Flush any remaining updates
        self.flush_status_buffer()
        self.flush_worker_updates()
    
    # Optimized update methods with batching
    def buffer_status_update(self, message):
        """Buffer status updates and flush periodically"""
        self.status_buffer.append(message.rstrip())
        # Start timer to flush buffer (100ms delay)
        self.status_timer.start(100)
    
    def flush_status_buffer(self):
        """Flush buffered status messages"""
        if self.status_buffer:
            # Join all messages and append in one operation
            combined_text = '\n'.join(self.status_buffer)
            self.status_log.append(combined_text)
            self.status_buffer.clear()
            
            # Auto-scroll to bottom
            scrollbar = self.status_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    def update_status(self, message):
        """Direct status update for non-threaded messages"""
        self.status_log.append(message.rstrip())
        scrollbar = self.status_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def update_progress(self, completed, total):
        """Update progress - this is called infrequently so no batching needed"""
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(completed)
        else:
            self.progress.setValue(0)
    
    def update_results_table(self, counts):
        """Update results table - batch these updates"""
        # Only update if values actually changed
        changed = False
        for i in range(min(self.results_table.rowCount(), len(counts))):
            count = counts.get(i, 0)
            if self.last_results_update.get(i, -1) != count:
                self.last_results_update[i] = count
                changed = True
        
        if changed:
            # Update all at once
            for i in range(min(self.results_table.rowCount(), len(counts))):
                count = counts.get(i, 0)
                item = self.results_table.item(i, 1)
                if item:
                    item.setText(str(count))
    
    def buffer_worker_update(self, workers_dict):
        """Buffer worker updates and flush periodically"""
        self.last_worker_update.update(workers_dict)
        # Start timer to flush worker updates (500ms delay)
        self.worker_timer.start(500)
    
    def flush_worker_updates(self):
        """Flush buffered worker updates"""
        if not self.last_worker_update:
            return
            
        for worker_idx, status in self.last_worker_update.items():
            if worker_idx < self.worker_table.rowCount():
                # Format status - truncate if too long
                status_text = str(status)
                if len(status_text) > 30:
                    status_text = status_text[:27] + "..."
                
                item = self.worker_table.item(worker_idx, 1)
                if item:
                    item.setText(status_text)
    
    def closeEvent(self, event):
        """Handle application close"""
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DatasheetGrabberGUI()
    window.show()
    sys.exit(app.exec())