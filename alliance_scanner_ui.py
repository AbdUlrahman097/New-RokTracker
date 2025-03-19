import logging
from dummy_root import get_app_root
from roktracker.alliance.additional_data import AdditionalData
from roktracker.alliance.governor_data import GovernorData
from roktracker.alliance.scanner import AllianceScanner
from roktracker.utils.check_python import check_py_version
from roktracker.utils.exception_handling import GuiExceptionHandler
from roktracker.utils.exceptions import AdbError, ConfigError
from roktracker.utils.general import is_string_int, load_config

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, 
    QProgressBar, QFrame, QVBoxLayout, QHBoxLayout, QLineEdit,
    QCheckBox, QTabWidget, QGridLayout, QMessageBox, QGroupBox,
    QSpacerItem, QSizePolicy, QSystemTrayIcon
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QMutex, QMutexLocker
from PyQt6.QtGui import QIntValidator, QIcon

logging.basicConfig(
    filename=str(get_app_root() / "alliance-scanner.log"),
    encoding="utf-8",
    format="%(asctime)s %(module)s %(levelname)s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

check_py_version((3, 11))

import json
import logging
import os
import sys
import threading
import datetime
import csv

from dummy_root import get_app_root
from roktracker.utils.validator import sanitize_scanname, validate_installation
from roktracker.utils.adb import get_bluestacks_port
from threading import ExceptHookArgs, Thread
from typing import Dict, List

logger = logging.getLogger(__name__)
ex_handler = GuiExceptionHandler(logger)

sys.excepthook = ex_handler.handle_exception
threading.excepthook = ex_handler.handle_thread_exception

def to_int_or(element, alternative):
    if element == "Skipped":
        return element

    try:
        return int(element)
    except ValueError:
        return alternative

from typing import List, Dict, Any, TypedDict

class CheckboxValue(TypedDict):
    name: str
    default: bool
    group: str

class CheckboxFrame(QFrame):
    def __init__(self, parent, values: List[CheckboxValue], groupName: str):
        super().__init__(parent)
        self.values = [x for x in values if x["group"] == groupName]
        self.checkboxes: List[QCheckBox] = []
        layout = QVBoxLayout(self)
        
        for value in self.values:
            checkbox = QCheckBox(value["name"])
            if value["default"]:
                checkbox.setChecked(True)
            layout.addWidget(checkbox)
            self.checkboxes.append(checkbox)
        
        self.setLayout(layout)

    def get(self) -> Dict[str, bool]:
        values = {}
        for checkbox in self.checkboxes:
            values.update({checkbox.text(): checkbox.isChecked()})
        return values

class HorizontalCheckboxFrame(QFrame):
    def __init__(self, parent, values: List[CheckboxValue], groupName: str, options_per_row: int):
        super().__init__(parent)
        self.values = [x for x in values if x["group"] == groupName]
        self.checkboxes: List[Dict[str, QCheckBox]] = []
        
        layout = QGridLayout(self)
        
        for i, value in enumerate(self.values):
            col = i % options_per_row
            row = (i // options_per_row) * 2
            
            label = QLabel(value["name"])
            layout.addWidget(label, row, col)
            
            checkbox = QCheckBox()
            if value["default"]:
                checkbox.setChecked(True)
            layout.addWidget(checkbox, row + 1, col)
            
            self.checkboxes.append({value["name"]: checkbox})
        
        self.setLayout(layout)

    def get(self) -> Dict[str, bool]:
        values = {}
        for checkbox in self.checkboxes:
            for k, v in checkbox.items():
                values.update({k: bool(v.isChecked())})
        return values

class BasicOptionsFame(QFrame):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config

        self.port_validator = QIntValidator(1024, 65535)
        self.amount_validator = QIntValidator(1, 10000)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)
        self.setLayout(main_layout)

        scan_group = QGroupBox("Scan Settings")
        scan_layout = QGridLayout()
        scan_layout.setSpacing(10)
        scan_group.setLayout(scan_layout)

        self.scan_uuid_label = QLabel("Scan UUID:")
        scan_layout.addWidget(self.scan_uuid_label, 0, 0)
        self.scan_uuid_var = "---"
        self.scan_uuid_label_2 = QLabel(self.scan_uuid_var)
        scan_layout.addWidget(self.scan_uuid_label_2, 0, 1)

        self.scan_name_label = QLabel("Scan name:")
        scan_layout.addWidget(self.scan_name_label, 1, 0)
        self.scan_name_text = QLineEdit()
        self.scan_name_text.setText(config["scan"]["kingdom_name"])
        scan_layout.addWidget(self.scan_name_text, 1, 1)

        self.scan_amount_label = QLabel("People to scan:")
        scan_layout.addWidget(self.scan_amount_label, 2, 0)
        self.scan_amount_text = QLineEdit()
        self.scan_amount_text.setValidator(self.amount_validator)
        self.scan_amount_text.setText(str(config["scan"]["people_to_scan"]))
        scan_layout.addWidget(self.scan_amount_text, 2, 1)

        main_layout.addWidget(scan_group)

        connection_group = QGroupBox("Connection Settings")
        connection_layout = QGridLayout()
        connection_layout.setSpacing(10)
        connection_group.setLayout(connection_layout)

        self.bluestacks_instance_label = QLabel("Bluestacks name:")
        connection_layout.addWidget(self.bluestacks_instance_label, 0, 0)
        self.bluestacks_instance_text = QLineEdit()
        self.bluestacks_instance_text.setText(config["general"]["bluestacks"]["name"])
        self.bluestacks_instance_text.textChanged.connect(self.update_port)
        connection_layout.addWidget(self.bluestacks_instance_text, 0, 1)

        self.adb_port_label = QLabel("Adb port:")
        connection_layout.addWidget(self.adb_port_label, 1, 0)
        self.adb_port_text = QLineEdit()
        self.adb_port_text.setValidator(self.port_validator)
        connection_layout.addWidget(self.adb_port_text, 1, 1)
        self.update_port()

        main_layout.addWidget(connection_group)

        output_group = QGroupBox("Output Formats")
        output_layout = QVBoxLayout()
        output_group.setLayout(output_layout)

        output_values: List[CheckboxValue] = [
            CheckboxValue(
                name="xlsx",
                default=config["scan"]["formats"]["xlsx"],
                group="Output Format"
            ),
            CheckboxValue(
                name="csv",
                default=config["scan"]["formats"]["csv"],
                group="Output Format"
            ),
            CheckboxValue(
                name="jsonl",
                default=config["scan"]["formats"]["jsonl"],
                group="Output Format"
            ),
        ]
        self.output_options = HorizontalCheckboxFrame(self, output_values, "Output Format", 3)
        output_layout.addWidget(self.output_options)

        main_layout.addWidget(output_group)
        
        main_layout.addStretch()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scan_name_text.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bluestacks_instance_text.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.adb_port_text.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scan_amount_text.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_uuid(self, uuid):
        self.scan_uuid_var = uuid
        self.scan_uuid_label_2.setText(uuid)

    def update_port(self, name=""):
        """Update port text field with Bluestacks port"""
        self.adb_port_text.clear()
        try:
            port = get_bluestacks_port(
                name if name else self.bluestacks_instance_text.text(), 
                self.config
            )
            self.adb_port_text.setText(str(port))
        except Exception as e:
            logger.error(f"Failed to get Bluestacks port: {e}")
            QMessageBox.warning(
                self,
                "Port Error",
                f"Failed to get Bluestacks port: {e}"
            )
        return True

    def get_options(self):
        formats = OutputFormats()
        formats.from_dict(self.output_options.get())
        return {
            "uuid": self.scan_uuid_var,
            "name": self.scan_name_text.text(),
            "port": int(self.adb_port_text.text()),
            "amount": int(self.scan_amount_text.text()),
            "formats": formats,
        }

    def options_valid(self) -> bool:
        val_errors: List[str] = []
        
        try:
            port = int(self.adb_port_text.text())
            if port < 1024 or port > 65535:
                val_errors.append("ADB port must be between 1024 and 65535")
        except ValueError:
            val_errors.append("ADB port must be a valid number")

        try:
            amount = int(self.scan_amount_text.text())
            if amount < 1 or amount > 10000:
                val_errors.append("People to scan must be between 1 and 10000")
        except ValueError:
            val_errors.append("People to scan must be a valid number")

        if all(value == False for value in self.output_options.get().values()):
            val_errors.append("No output format checked")

        if len(val_errors) > 0:
            QMessageBox.warning(
                self,
                "Invalid input",
                "\n".join(val_errors)
            )
            return False

        name_validation = sanitize_scanname(self.scan_name_text.text())
        if not name_validation.valid:
            QMessageBox.warning(
                self,
                "Name is not valid",
                f"Name is not valid and got changed to:\n{name_validation.result}\n"
                + f"Please check the new name and press start again."
            )
            self.scan_name_text.clear()
            self.scan_name_text.setText(name_validation.result)
            return False

        return True


class ScanOptionsFrame(QFrame):
    def __init__(self, parent, values: List[CheckboxValue]):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.setLayout(layout)
        self.values = values
        self.first_screen_options_frame = CheckboxFrame(self, values, "First Screen")
        self.second_screen_options_frame = CheckboxFrame(self, values, "Second Screen")
        self.third_screen_options_frame = CheckboxFrame(self, values, "Third Screen")

        layout.addWidget(self.first_screen_options_frame)
        layout.addWidget(self.second_screen_options_frame)
        layout.addWidget(self.third_screen_options_frame)

    def get(self) -> Dict[str, bool]:
        options = {}
        options.update(self.first_screen_options_frame.get())
        options.update(self.second_screen_options_frame.get())
        options.update(self.third_screen_options_frame.get())
        return options


class AdditionalStatusInfo(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.values: Dict[str, QLabel] = {}
        layout = QGridLayout(self)
        self.setLayout(layout)

        self.gov_number_var = QLabel("24 to 30 of 30")
        self.values.update({"govs": self.gov_number_var})
        self.approx_time_remaining_var = QLabel("0:16:34")
        self.values.update({"eta": self.approx_time_remaining_var})
        self.last_time_var = QLabel("13:55:30")
        self.values.update({"time": self.last_time_var})

        self.last_time = QLabel("Current time")
        layout.addWidget(self.last_time, 0, 0)
        layout.addWidget(self.last_time_var, 1, 0)

        self.eta = QLabel("ETA")
        layout.addWidget(self.eta, 0, 2)
        layout.addWidget(self.approx_time_remaining_var, 1, 2)

        layout.addWidget(self.gov_number_var, 0, 1)

    def set_var(self, key, value):
        if key in self.values:
            self.values[key].setText(value)


class LastBatchInfo(QFrame):
    def __init__(self, parent, govs_per_batch):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)
        self.setLayout(main_layout)

        status_group = QGroupBox("Current Status")
        status_layout = QVBoxLayout()
        status_group.setLayout(status_layout)

        self.additional_stats = AdditionalStatusInfo(self)
        status_layout.addWidget(self.additional_stats)

        main_layout.addWidget(status_group)

        govs_group = QGroupBox("Current Batch")
        govs_layout = QVBoxLayout()
        govs_layout.setSpacing(8)
        govs_group.setLayout(govs_layout)

        self.entries: List[QLabel] = []
        self.labels: List[QLabel] = []
        self.variables: Dict[str, QLabel] = {}

        for i in range(0, govs_per_batch):
            row_widget = QWidget()
            row_layout = QHBoxLayout()
            row_layout.setSpacing(10)
            row_widget.setLayout(row_layout)

            label = QLabel()
            entry = QLabel()
            
            label.setMinimumWidth(150)
            entry.setMinimumWidth(80)
            
            row_layout.addWidget(label)
            row_layout.addWidget(entry)
            row_layout.addStretch()

            govs_layout.addWidget(row_widget)

            self.variables.update({f"name-{i}": label})
            self.variables.update({f"score-{i}": entry})
            self.labels.append(label)
            self.entries.append(entry)

        main_layout.addWidget(govs_group)
        main_layout.addStretch()

    def set(self, values):
        for key, value in values.items():
            if key in self.variables:
                if isinstance(value, int):
                    self.variables[key].setText(f"{value:,}")
                else:
                    self.variables[key].setText(value)
            else:
                self.additional_stats.set_var(key, value)


class App(QMainWindow):
    update_ui_signal = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        self.ui_mutex = QMutex()
        self.scanner_thread = None
        self.alliance_scanner = None

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("images/alliance.png"))
        self.tray_icon.setToolTip("Alliance Scanner")
        self.tray_icon.show()
        
        self.update_ui_signal.connect(self.update_ui_safely)
        self.setup_ui()

    def show_notification(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information):
        """Show both a system notification and a message box"""
        self.tray_icon.showMessage(title, message, icon, 5000)

        if icon == QSystemTrayIcon.MessageIcon.Critical:
            QMessageBox.critical(self, title, message)
        else:
            QMessageBox.information(self, title, message)

    def closeEvent(self, event):
        """Handle application closing"""
        if self.alliance_scanner:
            try:
                with QMutexLocker(self.ui_mutex):
                    self.alliance_scanner.end_scan()
                    self.alliance_scanner = None
            except:
                logger.error("Failed to cleanly stop scanner")
        
        if self.scanner_thread and self.scanner_thread.is_alive():
            try:
                self.scanner_thread.join(timeout=2.0)
                if self.scanner_thread.is_alive():
                    logger.warning("Scanner thread did not terminate cleanly")
            except:
                logger.error("Failed to join scanner thread")
        
        self.scanner_thread = None
        self.update_ui_signal.disconnect()
        
        super().closeEvent(event)

    def setup_ui(self):
        """Initialize the UI components"""
        file_validation = validate_installation()
        if not file_validation.success:
            QMessageBox.critical(
                self,
                "Validation failed",
                "\n".join(file_validation.messages)
            )
            self.close()
            return

        try:
            self.config = load_config()
        except ConfigError as e:
            logger.fatal(str(e))
            QMessageBox.critical(
                self,
                "Invalid Config",
                str(e)
            )
            self.close()
            return

        self.setWindowTitle("Alliance Scanner")
        self.setGeometry(100, 100, 800, 500)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)

        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setSpacing(15)
        left_panel.setLayout(left_layout)

        self.options_frame = BasicOptionsFame(self, self.config)
        left_layout.addWidget(self.options_frame)

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)

        self.start_scan_button = QPushButton("Start Scan")
        self.start_scan_button.clicked.connect(self.start_scan)
        self.start_scan_button.setMinimumHeight(40)
        buttons_layout.addWidget(self.start_scan_button)

        self.end_scan_button = QPushButton("End Scan")
        self.end_scan_button.clicked.connect(self.end_scan)
        self.end_scan_button.setMinimumHeight(40)
        buttons_layout.addWidget(self.end_scan_button)

        left_layout.addLayout(buttons_layout)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setSpacing(15)
        right_panel.setLayout(right_layout)

        self.last_batch_frame = LastBatchInfo(self, 6)
        right_layout.addWidget(self.last_batch_frame)

        progress_group = QGroupBox("Scan Progress")
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(10)
        progress_group.setLayout(progress_layout)

        self.current_state = QLabel("Not started")
        self.current_state.setStyleSheet("font-size: 14px; font-weight: bold;")
        progress_layout.addWidget(self.current_state)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(30)
        progress_layout.addWidget(self.progress_bar)
        self.progress_bar.setValue(0)

        right_layout.addWidget(progress_group)

        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        right_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(left_panel, stretch=4)
        main_layout.addWidget(right_panel, stretch=6)

        QWidget.setTabOrder(self.options_frame.scan_name_text, self.options_frame.bluestacks_instance_text)
        QWidget.setTabOrder(self.options_frame.bluestacks_instance_text, self.options_frame.adb_port_text)
        QWidget.setTabOrder(self.options_frame.adb_port_text, self.options_frame.scan_amount_text)
        QWidget.setTabOrder(self.options_frame.scan_amount_text, self.start_scan_button)
        QWidget.setTabOrder(self.start_scan_button, self.end_scan_button)

        self.options_frame.scan_name_text.setFocus()

    def close_program(self):
        self.close()

    def end_scan(self):
        """Handle scan termination"""
        with QMutexLocker(self.ui_mutex):
            if self.alliance_scanner:
                self.alliance_scanner.end_scan()
                self.end_scan_button.setEnabled(False)
                self.end_scan_button.setText("Abort after next governor")

    def start_scan(self):
        """Start scan in a new thread"""
        with QMutexLocker(self.ui_mutex):
            if self.scanner_thread and self.scanner_thread.is_alive():
                return
                
            self.scanner_thread = Thread(
                target=self.launch_scanner,
                name="ScannerThread",
                daemon=True
            )
            self.scanner_thread.start()

    def launch_scanner(self):
        if not self.options_frame.options_valid():
            return

        self.start_scan_button.setEnabled(False)
        options = self.options_frame.get_options()

        try:
            self.alliance_scanner = AllianceScanner(options["port"], self.config)
            self.alliance_scanner.set_batch_callback(self.governor_callback)
            self.alliance_scanner.set_state_callback(self.state_callback)
            self.options_frame.set_uuid(self.alliance_scanner.run_id)

            logger.info(f"Scan started at {datetime.datetime.now()}")
            self.alliance_scanner.start_scan(
                options["name"], options["amount"], options["formats"]
            )

        except AdbError as error:
            logger.error(f"ADB connection error at {datetime.datetime.now()}: {error}")
            error_msg = (
                "Failed to connect to BlueStacks via ADB. Please follow these troubleshooting steps:\n\n"
                "1. Verify BlueStacks:\n"
                "   - Check that BlueStacks is running\n"
                "   - Confirm the instance name matches exactly: '{name}'\n"
                "   - Try restarting BlueStacks\n\n"
                "2. Check ADB Connection:\n"
                "   - Verify ADB port {port} matches your BlueStacks instance\n"
                "   - Run 'adb devices' to check connected devices\n"
                "   - Try 'adb kill-server' followed by 'adb start-server'\n\n"
                "3. Network/Firewall:\n"
                "   - Check if firewall is blocking ADB connections\n"
                "   - Ensure no other program is using port {port}\n\n"
                "4. Game State:\n"
                "   - Verify you're logged into Rise of Kingdoms\n"
                "   - Ensure alliance view is open and accessible\n"
                "   - Check your internet connection\n\n"
                "5. Tools/Environment:\n"
                "   - Verify platform-tools (adb.exe) exists in deps folder\n"
                "   - Check if running as administrator helps\n\n"
                "Error details: {error}"
            ).format(name=options.get("name", ""), port=options["port"], error=str(error))
            self.update_ui_signal.emit({
                "error": "ADB Connection Error",
                "message": error_msg
            })
            self.state_callback("Not started - ADB Error")

        except ConfigError as error:
            logger.error(f"Configuration error at {datetime.datetime.now()}: {error}")
            error_msg = (
                "Configuration error detected. Please check the following:\n\n"
                "1. Config File:\n"
                "   - Verify config.json exists in the application root\n"
                "   - Check file permissions (read/write access)\n"
                "   - Validate JSON syntax is correct\n\n"
                "2. Required Settings:\n"
                "   - Confirm all required settings are present\n"
                "   - Check paths for alliance scanner are configured\n"
                "   - Verify BlueStacks configuration is correct\n\n"
                "3. File Structure:\n"
                "   - Check if all required folders exist (deps, tessdata)\n"
                "   - Verify no required files are missing\n\n"
                "4. Workspace:\n"
                "   - Ensure working directory is writable\n"
                "   - Check if log files can be created/written\n\n"
                "5. Try These Steps:\n"
                "   - Reset config.json to default values\n"
                "   - Run application as administrator\n"
                "   - Check application logs for details\n\n"
                "Error details: {error}"
            ).format(error=str(error))
            self.update_ui_signal.emit({
                "error": "Configuration Error",
                "message": error_msg
            })
            self.state_callback("Not started - Config Error")

        except Exception as error:
            logger.error(f"Unexpected error at {datetime.datetime.now()}: {error}")
            error_msg = (
                "An unexpected error occurred. Please try:\n\n"
                "1. Restarting BlueStacks\n"
                "2. Opening and closing alliance menu\n"
                "3. Checking your internet connection\n"
                "4. Ensuring you have alliance membership\n"
                "5. Restarting the scanner application\n\n"
                "Error details: {error}"
            ).format(error=str(error))
            self.update_ui_signal.emit({
                "error": "Unexpected Error",
                "message": error_msg
            })
            self.state_callback("Not started - Fatal Error")
        else:
            logger.info(f"Scan completed at {datetime.datetime.now()}")
            self.update_ui_signal.emit({
                "info": "Scan Complete",
                "message": "The scan has been completed successfully."
            })
        finally:
            self.end_scan_button.setEnabled(True)
            self.end_scan_button.setText("End scan")
            self.start_scan_button.setEnabled(True)

    def update_ui_safely(self, data):
        """Handle UI updates in a thread-safe way"""
        with QMutexLocker(self.ui_mutex):
            if "error" in data:
                self.show_notification(
                    data["error"],
                    data["message"],
                    QSystemTrayIcon.MessageIcon.Critical
                )
            elif "info" in data:
                self.show_notification(
                    data["info"],
                    data["message"],
                    QSystemTrayIcon.MessageIcon.Information
                )
            elif "batch_data" in data:
                self.last_batch_frame.set(data["batch_data"])
            elif "progress" in data:
                progress = data["progress"]
                progress_value = round((progress["current"] / progress["total"]) * 100)
                self.progress_bar.setValue(progress_value)
                
                if progress_value in [25, 50, 75, 100]:
                    self.tray_icon.showMessage(
                        "Scan Progress",
                        f"Alliance scan is {progress_value}% complete",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000
                    )
            elif "state" in data:
                self.current_state.setText(data["state"])

    def governor_callback(self, gov_data: List[GovernorData], extra_data: AdditionalData):
        batch_data: Dict[str, str | int] = {}
        for index, gov in enumerate(gov_data):
            batch_data.update({f"name-{index}": gov.name})
            batch_data.update({f"score-{index}": to_int_or(gov.score, "Unknown")})

        batch_data.update({
            "govs": f"{extra_data.current_page * extra_data.govs_per_page} to {(extra_data.current_page + 1) * extra_data.govs_per_page} of {extra_data.target_governor}",
            "time": extra_data.current_time,
            "eta": extra_data.eta(),
        })

        self.update_ui_signal.emit({"batch_data": batch_data})
        
        total_pages = extra_data.target_governor // extra_data.govs_per_page
        if extra_data.target_governor % extra_data.govs_per_page != 0:
            total_pages += 1
        self.update_ui_signal.emit({
            "progress": {
                "current": extra_data.current_page,
                "total": total_pages
            }
        })

    def state_callback(self, state):
        self.update_ui_signal.emit({"state": state})


class OutputFormats:
    def __init__(self):
        self.xlsx = False
        self.csv = False
        self.jsonl = False

    def from_dict(self, data):
        self.xlsx = data.get("xlsx", False)
        self.csv = data.get("csv", False)
        self.jsonl = data.get("jsonl", False)

    def to_dict(self):
        return {
            "xlsx": self.xlsx,
            "csv": self.csv,
            "jsonl": self.jsonl,
        }
