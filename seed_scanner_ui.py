import logging
import datetime
import sys
import threading
from dummy_root import get_app_root
from roktracker.alliance.additional_data import AdditionalData
from roktracker.alliance.governor_data import GovernorData
from roktracker.seed.scanner import SeedScanner
from roktracker.utils.check_python import check_py_version
from roktracker.utils.exception_handling import GuiExceptionHandler
from roktracker.utils.exceptions import AdbError, ConfigError
from roktracker.utils.general import is_string_int, load_config

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, 
    QProgressBar, QFrame, QVBoxLayout, QHBoxLayout, QLineEdit,
    QCheckBox, QTabWidget, QGridLayout, QMessageBox, QGroupBox,
    QSpacerItem, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QMutex, QMutexLocker
from PyQt6.QtGui import QIntValidator

logging.basicConfig(
    filename=str(get_app_root() / "seed-scanner.log"),
    encoding="utf-8",
    format="%(asctime)s %(module)s %(levelname)s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

check_py_version((3, 11))

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

# Convert CheckboxFrame class to use PyQt
from typing import List, Dict, Any, TypedDict

class CheckboxValue(TypedDict):
    name: str
    default: bool
    group: str

class CheckboxFrame(QFrame):
    def __init__(self, parent, values: List[CheckboxValue], groupName: str):
        super().__init__(parent)
        # Properly type the filter lambda
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

# Convert HorizontalCheckboxFrame class to use PyQt
class HorizontalCheckboxFrame(QFrame):
    def __init__(self, parent, values: List[CheckboxValue], groupName: str, options_per_row: int):
        super().__init__(parent)
        self.values = [x for x in values if x["group"] == groupName]
        self.checkboxes: List[Dict[str, QCheckBox]] = []
        
        layout = QGridLayout(self)
        
        # In QGridLayout, the order is (row, column)
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

# Reuse the same BasicOptionsFrame, AdditionalStatusInfo and other UI components as honor_scanner_ui.py
# Just copying them here for consistency and changing any honor-specific references to seed

class BasicOptionsFame(QFrame):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config

        # Set validators with ranges
        self.port_validator = QIntValidator(1024, 65535)  # Common port range
        self.amount_validator = QIntValidator(1, 10000)   # Reasonable scan amount range

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)  # Add space between sections
        self.setLayout(main_layout)

        # Scan Settings Group
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

        # Connection Settings Group
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

        # Output Settings Group
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
        
        # Add some stretching space at the bottom
        main_layout.addStretch()

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
                f"Failed to get Bluestacks port: {e}")
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

        layout.addWidget(QLabel("Current time"), 0, 0)
        layout.addWidget(self.last_time_var, 1, 0)

        layout.addWidget(QLabel("ETA"), 0, 2)
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

        # Status Group
        status_group = QGroupBox("Current Status")
        status_layout = QVBoxLayout()
        status_group.setLayout(status_layout)

        self.additional_stats = AdditionalStatusInfo(self)
        status_layout.addWidget(self.additional_stats)

        main_layout.addWidget(status_group)

        # Governors Group
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
            
            # Set minimum width for better alignment
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
        # Enable Qt attributes for better performance
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        # Thread safety
        self.ui_mutex = QMutex()
        self.scanner_thread = None
        self.seed_scanner = None
        
        # Setup signal connection
        self.update_ui_signal.connect(self.update_ui_safely)
        self.setup_ui()

    def closeEvent(self, event):
        """Handle application closing"""
        if self.seed_scanner:
            try:
                with QMutexLocker(self.ui_mutex):
                    self.seed_scanner.end_scan()
                    self.seed_scanner = None
            except:
                logger.error("Failed to cleanly stop scanner")
        
        if self.scanner_thread and self.scanner_thread.is_alive():
            try:
                self.scanner_thread.join(timeout=2.0)
                if self.scanner_thread.is_alive():
                    logger.warning("Scanner thread did not terminate cleanly")
            except:
                logger.error("Failed to join scanner thread")
        
        # Clean up references
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

        self.setWindowTitle("Seed Scanner")
        self.setGeometry(100, 100, 800, 500)  # Match honor scanner window size

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Left side - Options
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setSpacing(15)
        left_panel.setLayout(left_layout)

        self.options_frame = BasicOptionsFame(self, self.config)
        left_layout.addWidget(self.options_frame)

        # Control buttons
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

        # Right side - Status and Progress
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setSpacing(15)
        right_panel.setLayout(right_layout)

        self.last_batch_frame = LastBatchInfo(self, 6)  # Seed scanner uses 6 governors per batch
        right_layout.addWidget(self.last_batch_frame)

        # Progress section
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

        # Add panels to main layout with size policies
        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        right_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(left_panel, stretch=4)
        main_layout.addWidget(right_panel, stretch=6)

        # Set proper tab order for keyboard navigation
        QWidget.setTabOrder(self.options_frame.scan_name_text, self.options_frame.bluestacks_instance_text)
        QWidget.setTabOrder(self.options_frame.bluestacks_instance_text, self.options_frame.adb_port_text)
        QWidget.setTabOrder(self.options_frame.adb_port_text, self.options_frame.scan_amount_text)
        QWidget.setTabOrder(self.options_frame.scan_amount_text, self.start_scan_button)
        QWidget.setTabOrder(self.start_scan_button, self.end_scan_button)

        # Set initial focus
        self.options_frame.scan_name_text.setFocus()

    def close_program(self):
        self.close()

    def end_scan(self):
        """Handle scan termination"""
        with QMutexLocker(self.ui_mutex):
            if self.seed_scanner:
                self.seed_scanner.end_scan()
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
                daemon=True  # Make thread daemon so it doesn't prevent app exit
            )
            self.scanner_thread.start()

    def launch_scanner(self):
        if not self.options_frame.options_valid():
            return

        self.start_scan_button.setEnabled(False)
        options = self.options_frame.get_options()

        try:
            self.seed_scanner = SeedScanner(options["port"], self.config)
            self.seed_scanner.set_batch_callback(self.governor_callback)
            self.seed_scanner.set_state_callback(self.state_callback)
            self.options_frame.set_uuid(self.seed_scanner.run_id)

            logger.info(f"Scan started at {datetime.datetime.now()}")
            self.seed_scanner.start_scan(
                options["name"],
                options["amount"],
                options["formats"]
            )

        except AdbError as error:
            logger.error(f"ADB connection error at {datetime.datetime.now()}: " + str(error))
            self.update_ui_signal.emit({
                "error": "ADB Connection Error",
                "message": "An error with the ADB connection occurred. Please verify that you are using the correct port and that your device is properly connected.\nExact message: " + str(error)
            })
            self.state_callback("Not started")
        except ConfigError as error:
            logger.error(f"Configuration error at {datetime.datetime.now()}: " + str(error))
            self.update_ui_signal.emit({
                "error": "Configuration Error",
                "message": "There was an error with the configuration. Please check your configuration settings.\nExact message: " + str(error)
            })
            self.state_callback("Not started")
        except Exception as error:
            logger.error(f"Unexpected error at {datetime.datetime.now()}: " + str(error))
            self.update_ui_signal.emit({
                "error": "Unexpected Error", 
                "message": "An unexpected error occurred. Please try again.\nExact message: " + str(error)
            })
            self.state_callback("Not started")
        else:
            logger.info(f"Scan completed at {datetime.datetime.now()}")
            self.update_ui_signal.emit({
                "success": True,
                "message": "The scan has been completed successfully."
            })
        finally:
            self.end_scan_button.setEnabled(True)
            self.end_scan_button.setText("End Scan")
            self.start_scan_button.setEnabled(True)

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

        self.last_batch_frame.set(batch_data)
        total_pages = extra_data.target_governor // extra_data.govs_per_page
        if extra_data.target_governor % extra_data.govs_per_page != 0:
            total_pages += 1
        # Explicitly convert float to int for progress bar
        progress_value = int((extra_data.current_page / total_pages) * 100)
        self.progress_bar.setValue(progress_value)

    def state_callback(self, state):
        self.current_state.setText(state)

    def update_ui_safely(self, data):
        if "error" in data:
            QMessageBox.critical(self, data["error"], data["message"])
        elif "success" in data:
            QMessageBox.information(self, "Success", data["message"])


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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())
