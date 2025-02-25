import logging
from dummy_root import get_app_root
from roktracker.utils.check_python import check_py_version
from roktracker.utils.exceptions import AdbError, ConfigError
from roktracker.utils.general import (
    is_string_float,
    is_string_int,
    load_config,
    to_int_check,
)
from roktracker.utils.gui import ConfirmDialog, InfoDialog
from roktracker.utils.output_formats import OutputFormats

logging.basicConfig(
    filename=str(get_app_root() / "kingdom-scanner.log"),
    encoding="utf-8",
    format="%(asctime)s %(module)s %(levelname)s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

check_py_version((3, 11))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, 
    QPushButton, QVBoxLayout, QHBoxLayout, QCheckBox,
    QFrame, QMessageBox, QProgressBar, QTabWidget,
    QCalendarWidget, QTimeEdit, QDialog, QGridLayout,
    QComboBox, QGroupBox, QSpacerItem, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDateTime, QTime, QTimer, QSize
import json
import os
import sys
import threading
import datetime
import csv
from datetime import date
from io import TextIOWrapper

from roktracker.kingdom.additional_data import AdditionalData
from roktracker.kingdom.governor_data import GovernorData
from roktracker.kingdom.scanner import KingdomScanner
from roktracker.utils.adb import get_bluestacks_port
from roktracker.utils.exception_handling import GuiExceptionHandler
from roktracker.utils.validator import validate_installation, sanitize_scanname
from threading import Thread
from typing import Dict, List, Any

logger = logging.getLogger(__name__)
ex_handler = GuiExceptionHandler(logger)

sys.excepthook = ex_handler.handle_exception
threading.excepthook = ex_handler.handle_thread_exception

def to_int_or(value, default):
    """Convert a value to int or return default if not possible"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

class CheckboxFrame(QFrame):
    def __init__(self, values: List[Dict[str, Any]], groupName: str):
        super().__init__()
        self.values = list(filter(lambda x: x["group"] == groupName, values))
        self.checkboxes = []
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        for value in self.values:
            checkbox = QCheckBox(value["name"])
            if value["default"]:
                checkbox.setChecked(True)
            layout.addWidget(checkbox)
            self.checkboxes.append(checkbox)

    def get(self):
        values = {}
        for checkbox in self.checkboxes:
            values.update({checkbox.text(): checkbox.isChecked()})
        return values

    def set(self, preferences):
        for checkbox in self.checkboxes:
            checkbox_name = checkbox.text()
            if checkbox_name in preferences:
                checkbox.setChecked(preferences[checkbox_name])

class HorizontalCheckboxFrame(QFrame):
    def __init__(self, values: List[Dict[str, Any]], groupName: str, options_per_row: int):
        super().__init__()
        self.values = list(filter(lambda x: x["group"] == groupName, values))
        self.checkboxes = []
        
        layout = QGridLayout()
        self.setLayout(layout)
        
        for i, value in enumerate(self.values):
            row = i // options_per_row * 2
            col = i % options_per_row
            
            label = QLabel(value["name"])
            layout.addWidget(label, row, col)
            
            checkbox = QCheckBox()
            if value["default"]:
                checkbox.setChecked(True)
            layout.addWidget(checkbox, row + 1, col)
            
            self.checkboxes.append({value["name"]: checkbox})

    def get(self):
        values = {}
        for checkbox in self.checkboxes:
            for k, v in checkbox.items():
                values.update({k: v.isChecked()})
        return values

class TimePickerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Schedule Scan")
        self.setModal(True)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Calendar
        self.calendar = QCalendarWidget()
        self.calendar.setMinimumDate(date.today())
        layout.addWidget(self.calendar)
        
        # Time selection
        time_frame = QFrame()
        time_layout = QHBoxLayout()
        time_frame.setLayout(time_layout)
        
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        time_layout.addWidget(QLabel("Time:"))
        time_layout.addWidget(self.time_edit)
        
        layout.addWidget(time_frame)
        
        # Buttons
        button_frame = QFrame()
        button_layout = QHBoxLayout()
        button_frame.setLayout(button_layout)
        
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        
        layout.addWidget(button_frame)

    def get_datetime(self):
        selected_date = self.calendar.selectedDate().toPyDate()
        selected_time = self.time_edit.time().toPyTime()
        return datetime.datetime.combine(selected_date, selected_time)

class ScheduleFrame(QFrame):
    def __init__(self):
        super().__init__()
        layout = QGridLayout()
        self.setLayout(layout)
        
        self.schedule_switch = QCheckBox()
        self.time_button = QPushButton("Set time")
        self.time_button.setEnabled(False)
        self.time_button.clicked.connect(self.set_time)
        
        layout.addWidget(QLabel("Schedule scan:"), 0, 0)
        layout.addWidget(self.schedule_switch, 0, 1)
        layout.addWidget(QLabel("Start time:"), 1, 0)
        layout.addWidget(self.time_button, 1, 1)
        
        self.scheduled_time = None
        self.schedule_switch.stateChanged.connect(self.toggle_schedule)

    def set_time(self):
        dialog = TimePickerDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            scheduled_time = dialog.get_datetime()
            if scheduled_time and scheduled_time < datetime.datetime.now():
                QMessageBox.critical(self, "Error", "Selected time is in the past")
                return
                
            self.scheduled_time = scheduled_time
            if scheduled_time:
                self.time_button.setText(
                    scheduled_time.strftime("%Y-%m-%d %H:%M")
                )

    def toggle_schedule(self):
        self.time_button.setEnabled(self.schedule_switch.isChecked())
        if not self.schedule_switch.isChecked():
            self.scheduled_time = None
            self.time_button.setText("Set time")

    def get_scheduled_time(self):
        if self.schedule_switch.isChecked() and self.scheduled_time:
            return self.scheduled_time
        return None

class BasicOptionsFrame(QFrame):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setMinimumWidth(400)

        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Scan Info Group
        scan_group = QGroupBox("Scan Information")
        scan_layout = QGridLayout()
        scan_group.setLayout(scan_layout)

        self.scan_uuid_label = QLabel("Scan UUID:")
        scan_layout.addWidget(self.scan_uuid_label, 0, 0)
        self.scan_uuid_var = QLabel("---")
        scan_layout.addWidget(self.scan_uuid_var, 0, 1)

        self.scan_name_label = QLabel("Scan name:")
        scan_layout.addWidget(self.scan_name_label, 1, 0)
        self.scan_name_text = QLineEdit()
        self.scan_name_text.setText(config["scan"]["kingdom_name"])
        scan_layout.addWidget(self.scan_name_text, 1, 1)

        self.scan_amount_label = QLabel("People to scan:")
        scan_layout.addWidget(self.scan_amount_label, 2, 0)
        self.scan_amount_text = QLineEdit()
        self.scan_amount_text.setText(str(config["scan"]["people_to_scan"]))
        scan_layout.addWidget(self.scan_amount_text, 2, 1)

        main_layout.addWidget(scan_group)

        # BlueStacks Settings Group
        bluestacks_group = QGroupBox("BlueStacks Settings")
        bluestacks_layout = QGridLayout()
        bluestacks_group.setLayout(bluestacks_layout)

        self.bluestacks_instance_label = QLabel("Instance name:")
        bluestacks_layout.addWidget(self.bluestacks_instance_label, 0, 0)
        self.bluestacks_instance_text = QLineEdit()
        self.bluestacks_instance_text.setText(config["general"]["bluestacks"]["name"])
        self.bluestacks_instance_text.textChanged.connect(lambda: self.update_port())
        bluestacks_layout.addWidget(self.bluestacks_instance_text, 0, 1)

        self.adb_port_label = QLabel("ADB port:")
        bluestacks_layout.addWidget(self.adb_port_label, 1, 0)
        self.adb_port_text = QLineEdit()
        bluestacks_layout.addWidget(self.adb_port_text, 1, 1)
        self.update_port()

        main_layout.addWidget(bluestacks_group)

        # Scan Options Group
        options_group = QGroupBox("Scan Options")
        options_layout = QGridLayout()
        options_group.setLayout(options_layout)

        row = 0
        self.resume_scan_checkbox = QCheckBox("Resume scan")
        if config["scan"]["resume"]:
            self.resume_scan_checkbox.setChecked(True)
        options_layout.addWidget(self.resume_scan_checkbox, row, 0)

        self.new_scroll_switch = QCheckBox("Advanced scroll")
        if config["scan"]["advanced_scroll"]:
            self.new_scroll_switch.setChecked(True)
        options_layout.addWidget(self.new_scroll_switch, row, 1)

        row += 1
        self.track_inactives_switch = QCheckBox("Track inactives")
        if config["scan"]["track_inactives"]:
            self.track_inactives_switch.setChecked(True)
        options_layout.addWidget(self.track_inactives_switch, row, 0)

        self.validate_kills_switch = QCheckBox("Validate kills")
        if config["scan"]["validate_kills"]:
            self.validate_kills_switch.setChecked(True)
        options_layout.addWidget(self.validate_kills_switch, row, 1)

        row += 1
        self.reconstruct_fails_switch = QCheckBox("Reconstruct kills")
        if config["scan"]["reconstruct_kills"]:
            self.reconstruct_fails_switch.setChecked(True)
        options_layout.addWidget(self.reconstruct_fails_switch, row, 0)

        self.validate_power_switch = QCheckBox("Validate power")
        if config["scan"]["validate_power"]:
            self.validate_power_switch.setChecked(True)
        options_layout.addWidget(self.validate_power_switch, row, 1)

        row += 1
        self.power_threshold_label = QLabel("Power tolerance:")
        options_layout.addWidget(self.power_threshold_label, row, 0)
        self.power_threshold_text = QLineEdit()
        self.power_threshold_text.setText(str(config["scan"]["power_threshold"]))
        options_layout.addWidget(self.power_threshold_text, row, 1)

        main_layout.addWidget(options_group)

        # Timing Settings Group
        timing_group = QGroupBox("Timing Settings")
        timing_layout = QGridLayout()
        timing_group.setLayout(timing_layout)

        self.info_close_label = QLabel("More info wait:")
        timing_layout.addWidget(self.info_close_label, 0, 0)
        self.info_close_text = QLineEdit()
        self.info_close_text.setText(str(config["scan"]["timings"]["info_close"]))
        timing_layout.addWidget(self.info_close_text, 0, 1)

        self.gov_close_label = QLabel("Governor wait:")
        timing_layout.addWidget(self.gov_close_label, 1, 0)
        self.gov_close_text = QLineEdit()
        self.gov_close_text.setText(str(config["scan"]["timings"]["gov_close"]))
        timing_layout.addWidget(self.gov_close_text, 1, 1)

        main_layout.addWidget(timing_group)

        # Schedule frame
        schedule_group = QGroupBox("Schedule")
        schedule_layout = QVBoxLayout()
        schedule_group.setLayout(schedule_layout)
        self.schedule_frame = ScheduleFrame()
        schedule_layout.addWidget(self.schedule_frame)
        main_layout.addWidget(schedule_group)

        # Output options
        output_group = QGroupBox("Output Formats")
        output_layout = QVBoxLayout()
        output_group.setLayout(output_layout)

        output_values = [
            {
                "name": "xlsx",
                "default": config["scan"]["formats"]["xlsx"],
                "group": "Output Format",
            },
            {
                "name": "csv",
                "default": config["scan"]["formats"]["csv"],
                "group": "Output Format",
            },
            {
                "name": "jsonl",
                "default": config["scan"]["formats"]["jsonl"],
                "group": "Output Format",
            },
        ]
        self.output_options = HorizontalCheckboxFrame(output_values, "Output Format", 3)
        output_layout.addWidget(self.output_options)
        main_layout.addWidget(output_group)

        # Add vertical spacer at the bottom to push everything up
        main_layout.addStretch()

    def set_uuid(self, uuid):
        self.scan_uuid_var.setText(uuid)

    def update_port(self, name=""):
        self.adb_port_text.clear()
        try:
            if name != "":
                port = get_bluestacks_port(name, self.config)
            else:
                port = get_bluestacks_port(self.bluestacks_instance_text.text(), self.config)
            self.adb_port_text.setText(str(port))
        except Exception as e:
            logger.error(f"Error getting port: {str(e)}")
            self.adb_port_text.setText("")

    def get_options(self):
        formats = OutputFormats()
        formats.from_dict(self.output_options.get())
        return {
            "uuid": self.scan_uuid_var.text(),
            "name": self.scan_name_text.text(),
            "port": int(self.adb_port_text.text()),
            "amount": int(self.scan_amount_text.text()),
            "resume": self.resume_scan_checkbox.isChecked(),
            "adv_scroll": self.new_scroll_switch.isChecked(),
            "inactives": self.track_inactives_switch.isChecked(),
            "validate_kills": self.validate_kills_switch.isChecked(),
            "reconstruct": self.reconstruct_fails_switch.isChecked(),
            "validate_power": self.validate_power_switch.isChecked(),
            "power_threshold": self.power_threshold_text.text(),
            "info_time": float(self.info_close_text.text()),
            "gov_time": float(self.gov_close_text.text()),
            "formats": formats,
        }

    def options_valid(self) -> bool:
        val_errors: List[str] = []

        if not is_string_int(self.adb_port_text.text()):
            val_errors.append("Adb port invalid")

        if not is_string_int(self.scan_amount_text.text()):
            val_errors.append("People to scan invalid")

        if not is_string_float(self.info_close_text.text()):
            val_errors.append("Info timing invalid")

        if not is_string_float(self.gov_close_text.text()):
            val_errors.append("Governor timing invalid")

        if (
            not is_string_int(self.power_threshold_text.text())
        ) and self.validate_power_switch.isChecked():
            val_errors.append("Power tolerance invalid")

        if all(value == False for value in self.output_options.get().values()):
            val_errors.append("No output format checked")

        if len(val_errors) > 0:
            QMessageBox.critical(self, "Invalid input", "\n".join(val_errors))

        name_valitation = sanitize_scanname(self.scan_name_text.text())
        if not name_valitation.valid:
            QMessageBox.critical(
                self,
                "Name is not valid",
                f"Name is not valid and got changed to:\n{name_valitation.result}\n"
                + f"Please check the new name and press start again.",
            )
            self.scan_name_text.setText(name_valitation.result)

        return len(val_errors) == 0 and name_valitation.valid

class ScanOptionsFrame(QFrame):
    def __init__(self, values):
        super().__init__()
        self.setMinimumWidth(300)
        layout = QVBoxLayout()
        self.setLayout(layout)
        self.values = values

        screens_group = QGroupBox("Scan Options")
        screens_layout = QVBoxLayout()
        screens_group.setLayout(screens_layout)

        self.first_screen_options_frame = CheckboxFrame(values, "First Screen")
        self.second_screen_options_frame = CheckboxFrame(values, "Second Screen")
        self.third_screen_options_frame = CheckboxFrame(values, "Third Screen")

        screens_layout.addWidget(self.first_screen_options_frame)
        screens_layout.addWidget(self.second_screen_options_frame)
        screens_layout.addWidget(self.third_screen_options_frame)
        layout.addWidget(screens_group)

        self.save_button = QPushButton("Save Preferences")
        self.save_button.setMinimumHeight(30)
        self.save_button.clicked.connect(self.save_preferences)
        layout.addWidget(self.save_button)
        layout.addStretch()

    def get(self):
        options = {}
        options.update(self.first_screen_options_frame.get())
        options.update(self.second_screen_options_frame.get())
        options.update(self.third_screen_options_frame.get())
        return options

    def save_preferences(self):
        preferences = self.get()
        with open("scan_preferences.json", "w") as f:
            json.dump(preferences, f)
        QMessageBox.information(self, "Preferences Saved", "Your scan preferences have been saved.")

class AdditionalStatusInfo(QFrame):
    def __init__(self):
        super().__init__()
        self.values: Dict[str, QLabel] = {}
        layout = QGridLayout()
        layout.setSpacing(10)  # Add more spacing between elements
        self.setLayout(layout)

        # Create labels with improved styling
        self.gov_number_var = QLabel("550 of 600")
        self.gov_number_var.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gov_number_var.setStyleSheet("font-size: 12pt; font-weight: bold;")
        self.values.update({"govs": self.gov_number_var})

        self.approx_time_remaining_var = QLabel("0:16:34")
        self.approx_time_remaining_var.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.approx_time_remaining_var.setStyleSheet("font-size: 12pt; color: #2060c0;")
        self.values.update({"eta": self.approx_time_remaining_var})

        self.govs_skipped_var = QLabel("Skipped: 20")
        self.govs_skipped_var.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.values.update({"skipped": self.govs_skipped_var})

        self.last_time_var = QLabel("13:55:30")
        self.last_time_var.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.values.update({"time": self.last_time_var})

        # Create header labels
        time_label = QLabel("Current Time")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_label.setStyleSheet("font-weight: bold;")

        progress_label = QLabel("Progress")
        progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_label.setStyleSheet("font-weight: bold;")

        eta_label = QLabel("Estimated Time")
        eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eta_label.setStyleSheet("font-weight: bold;")

        # Layout the widgets in a grid
        layout.addWidget(time_label, 0, 0)
        layout.addWidget(progress_label, 0, 1)
        layout.addWidget(eta_label, 0, 2)

        layout.addWidget(self.last_time_var, 1, 0)
        layout.addWidget(self.gov_number_var, 1, 1)
        layout.addWidget(self.approx_time_remaining_var, 1, 2)
        
        # Add skipped count in its own row
        layout.addWidget(self.govs_skipped_var, 2, 1)

    def set_var(self, key, value):
        if key in self.values:
            self.values[key].setText(value)

class LastGovernorInfo(QFrame):
    def __init__(self, values):
        super().__init__()
        self.setMinimumWidth(350)
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        
        # Governor info group
        gov_group = QGroupBox("Governor Information")
        gov_layout = QGridLayout()
        gov_group.setLayout(gov_layout)
        
        self.values = values
        self.entries: List[QLabel] = []
        self.labels: List[QLabel] = []
        self.variables: Dict[str, QLabel] = {}

        for i, value in enumerate(self.values):
            variable = QLabel()
            variable.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label = QLabel(value["name"])
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            gov_layout.addWidget(label, i, 0)
            gov_layout.addWidget(variable, i, 1)

            self.variables.update({value["name"]: variable})
            self.labels.append(label)
            self.entries.append(variable)

        main_layout.addWidget(gov_group)
        
        # Stats group with current state
        stats_group = QGroupBox("Scan Status")
        stats_layout = QVBoxLayout()
        stats_group.setLayout(stats_layout)
        
        self.additional_stats = AdditionalStatusInfo()
        stats_layout.addWidget(self.additional_stats)

        self.current_state = QLabel("Not started")
        self.current_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_state.setStyleSheet("font-weight: bold; color: #404040; padding-top: 8px;")
        stats_layout.addWidget(self.current_state)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(25)
        stats_layout.addWidget(self.progress_bar)
        self.progress_bar.setValue(0)
        
        main_layout.addWidget(stats_group)
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
    # Add signals for thread-safe UI updates
    update_ui_signal = pyqtSignal(dict)
    update_state_signal = pyqtSignal(str)
    schedule_scan_signal = pyqtSignal(datetime.datetime)
    
    def __init__(self):
        super().__init__()
        # Connect signals to slots
        self.update_ui_signal.connect(self._update_ui)
        self.update_state_signal.connect(self._update_state)
        self.schedule_scan_signal.connect(self._handle_scheduled_scan)
        
        self.log_file: TextIOWrapper | None = None

        file_validation = validate_installation()
        if not file_validation.success:
            QMessageBox.critical(self, "Validation failed", "\n".join(file_validation.messages))
            self.close()

        try:
            self.config = load_config()
        except ConfigError as e:
            logger.fatal(str(e))
            QMessageBox.critical(self, "Invalid Config", str(e))
            self.close()

        self.setWindowTitle("Kingdom Scanner by Cyrexxis")
        self.setGeometry(100, 100, 900, 650)  # Reduced window size

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)

        # Left panel: Scan options with controls
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_panel.setLayout(left_layout)
        
        self.scan_options_frame = ScanOptionsFrame(
            [
                {"name": "ID", "default": True, "group": "First Screen"},
                {"name": "Name", "default": True, "group": "First Screen"},
                {"name": "Power", "default": True, "group": "First Screen"},
                {"name": "Killpoints", "default": True, "group": "First Screen"},
                {"name": "Alliance", "default": True, "group": "First Screen"},
                {"name": "T1 Kills", "default": True, "group": "Second Screen"},
                {"name": "T2 Kills", "default": True, "group": "Second Screen"},
                {"name": "T3 Kills", "default": True, "group": "Second Screen"},
                {"name": "T4 Kills", "default": True, "group": "Second Screen"},
                {"name": "T5 Kills", "default": True, "group": "Second Screen"},
                {"name": "Ranged", "default": True, "group": "Second Screen"},
                {"name": "Deads", "default": True, "group": "Third Screen"},
                {"name": "Rss Assistance", "default": True, "group": "Third Screen"},
                {"name": "Rss Gathered", "default": True, "group": "Third Screen"},
                {"name": "Helps", "default": True, "group": "Third Screen"},
            ],
        )
        left_layout.addWidget(self.scan_options_frame)

        # Control buttons under scan options
        controls_group = QGroupBox("Scan Controls")
        controls_layout = QHBoxLayout()
        controls_group.setLayout(controls_layout)

        self.start_scan_button = QPushButton("Start Scan")
        self.start_scan_button.setMinimumHeight(40)
        self.start_scan_button.clicked.connect(self.start_scan)
        controls_layout.addWidget(self.start_scan_button)

        self.end_scan_button = QPushButton("End Scan")
        self.end_scan_button.setMinimumHeight(40)
        self.end_scan_button.setEnabled(False)
        self.end_scan_button.clicked.connect(self.end_scan)
        controls_layout.addWidget(self.end_scan_button)
        
        left_layout.addWidget(controls_group)
        main_layout.addWidget(left_panel)

        # Center panel: Basic options
        center_panel = QWidget()
        center_layout = QVBoxLayout()
        center_panel.setLayout(center_layout)
        
        self.options_frame = BasicOptionsFrame(self.config)
        center_layout.addWidget(self.options_frame)
        main_layout.addWidget(center_panel)

        # Right panel: Governor info with status and current state
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_panel.setLayout(right_layout)

        self.last_gov_frame = LastGovernorInfo(
            [
                {"name": "ID", "col": 0},
                {"name": "Name", "col": 0},
                {"name": "Power", "col": 0},
                {"name": "Killpoints", "col": 0},
                {"name": "T1 Kills", "col": 0},
                {"name": "T2 Kills", "col": 0},
                {"name": "T3 Kills", "col": 0},
                {"name": "T4 Kills", "col": 0},
                {"name": "T5 Kills", "col": 0},
                {"name": "T4+5 Kills", "col": 0},
                {"name": "Total Kills", "col": 0},
                {"name": "Ranged", "col": 0},
                {"name": "Dead", "col": 0},
                {"name": "Rss Assistance", "col": 0},
                {"name": "Rss Gathered", "col": 0},
                {"name": "Helps", "col": 0},
                {"name": "Alliance", "col": 0},
            ],
        )
        right_layout.addWidget(self.last_gov_frame)
        main_layout.addWidget(right_panel)
        
        # Set stretch factors for panels
        main_layout.setStretch(0, 1)  # Left panel
        main_layout.setStretch(1, 2)  # Center panel
        main_layout.setStretch(2, 1)  # Right panel

        self.load_preferences()

    def ask_confirm(self, msg) -> bool:
        result = QMessageBox.question(self, "No Governor found", msg)
        return result == QMessageBox.StandardButton.Yes

    def close_program(self):
        self.close()

    def start_scan(self):
        scheduled_time = self.options_frame.schedule_frame.get_scheduled_time()
        if scheduled_time:
            wait_seconds = (scheduled_time - datetime.datetime.now()).total_seconds()
            if wait_seconds > 0:
                # Emit signal to handle scheduling in main thread
                self.schedule_scan_signal.emit(scheduled_time)
                return
        Thread(target=self.launch_scanner).start()

    def _handle_scheduled_scan(self, scheduled_time):
        # This runs in the main thread
        self.state_callback(f"Waiting to start at {scheduled_time.strftime('%H:%M')}")
        wait_ms = int((scheduled_time - datetime.datetime.now()).total_seconds() * 1000)
        QTimer.singleShot(wait_ms, self.launch_scanner)

    def launch_scanner(self):
        if not self.options_frame.options_valid():
            return

        # Create scanner and objects in the thread where they'll be used
        self.start_scan_button.setEnabled(False)
        scan_options = self.scan_options_frame.get()
        options = self.options_frame.get_options()

        self.config["scan"]["timings"]["info_close"] = options["info_time"]
        self.config["scan"]["timings"]["gov_close"] = options["gov_time"]
        self.config["scan"]["advanced_scroll"] = options["adv_scroll"]

        try:
            self.end_scan_button.setEnabled(True)
            self.end_scan_button.setText("End scan")

            # Create scanner in the worker thread
            self.kingdom_scanner = KingdomScanner(
                self.config, scan_options, options["port"]
            )
            self.kingdom_scanner.set_governor_callback(self.governor_callback)
            self.kingdom_scanner.set_state_callback(self.state_callback)
            self.kingdom_scanner.set_continue_handler(self.ask_confirm)
            
            # Set UUID through signal to be thread-safe
            self.update_ui_signal.emit({"uuid": self.kingdom_scanner.run_id})

            logger.info(f"Scan started at {datetime.datetime.now()}")
            self.kingdom_scanner.start_scan(
                options["name"],
                options["amount"],
                options["resume"],
                options["inactives"],
                options["validate_kills"],
                options["reconstruct"],
                options["validate_power"],
                options["power_threshold"],
                options["formats"],
            )

        except AdbError as error:
            logger.error(
                f"ADB connection error at {datetime.datetime.now()}: " + str(error)
            )
            QMessageBox.critical(
                self,
                "ADB Connection Error",
                "An error with the ADB connection occurred. Please verify that you are using the correct port and that your device is properly connected.\nExact message: "
                + str(error),
            )
            self.state_callback("Not started")
        except ConfigError as error:
            logger.error(
                f"Configuration error at {datetime.datetime.now()}: " + str(error)
            )
            QMessageBox.critical(
                self,
                "Configuration Error",
                "There was an error with the configuration. Please check your configuration settings.\nExact message: "
                + str(error),
            )
            self.state_callback("Not started")
        except Exception as error:
            logger.error(
                f"Unexpected error at {datetime.datetime.now()}: " + str(error)
            )
            QMessageBox.critical(
                self,
                "Unexpected Error",
                "An unexpected error occurred. Please try again.\nExact message: "
                + str(error),
            )
            self.state_callback("Not started")
        else:
            logger.info(f"Scan completed at {datetime.datetime.now()}")
            QMessageBox.information(self, "Scan Complete", "The scan has been completed successfully.")
        finally:
            self.end_scan_button.setEnabled(False)
            self.end_scan_button.setText("End scan")
            self.start_scan_button.setEnabled(True)

    def end_scan(self):
        self.kingdom_scanner.end_scan()
        self.end_scan_button.setEnabled(False)
        self.end_scan_button.setText("Abort after next governor")

    def governor_callback(self, gov_data: GovernorData, extra_data: AdditionalData):
        skipped_text = f"{extra_data.skipped_governors} skips"
        if extra_data.skipped_governors == 1:
            skipped_text = f"{extra_data.skipped_governors} skip"

        # Emit signal instead of directly updating UI
        self.update_ui_signal.emit({
            "governor_data": {
                "ID": gov_data.id,
                "Name": gov_data.name,
                "Power": to_int_or(gov_data.power, "Unknown"),
                "Killpoints": to_int_or(gov_data.killpoints, "Unknown"),
                "Dead": to_int_or(gov_data.dead, "Unknown"),
                "T1 Kills": to_int_or(gov_data.t1_kills, "Unknown"),
                "T2 Kills": to_int_or(gov_data.t2_kills, "Unknown"),
                "T3 Kills": to_int_or(gov_data.t3_kills, "Unknown"),
                "T4 Kills": to_int_or(gov_data.t4_kills, "Unknown"),
                "T5 Kills": to_int_or(gov_data.t5_kills, "Unknown"),
                "T4+5 Kills": to_int_or(gov_data.t45_kills(), "Unknown"),
                "Total Kills": to_int_or(gov_data.total_kills(), "Unknown"),
                "Ranged": to_int_or(gov_data.ranged_points, "Unknown"),
                "Rss Assistance": to_int_or(gov_data.rss_assistance, "Unknown"),
                "Rss Gathered": to_int_or(gov_data.rss_gathered, "Unknown"),
                "Helps": to_int_or(gov_data.helps, "Unknown"),
                "Alliance": gov_data.alliance,
            },
            "extra_data": {
                "govs": f"{extra_data.current_governor} of {extra_data.target_governor}",
                "skipped": skipped_text,
                "time": extra_data.current_time,
                "eta": extra_data.eta(),
            },
            "progress": {
                "current": extra_data.current_governor,
                "total": extra_data.target_governor
            }
        })

    def _update_ui(self, data):
        # Actual UI updates happen in main thread
        if "uuid" in data:
            self.options_frame.set_uuid(data["uuid"])
            
        if "governor_data" in data:
            for key, value in data["governor_data"].items():
                if key in self.last_gov_frame.variables:
                    if isinstance(value, int):
                        self.last_gov_frame.variables[key].setText(f"{value:,}")
                    else:
                        self.last_gov_frame.variables[key].setText(str(value))

        if "extra_data" in data:
            for key, value in data["extra_data"].items():
                self.last_gov_frame.additional_stats.set_var(key, value)

        if "progress" in data:
            progress = int((data["progress"]["current"] / data["progress"]["total"]) * 100)
            self.last_gov_frame.progress_bar.setValue(progress)

    def state_callback(self, state):
        # Emit signal instead of directly updating UI
        self.update_state_signal.emit(state)

    def _update_state(self, state):
        # Actual UI update happens in main thread
        self.last_gov_frame.current_state.setText(state)

    def update_progress(self, current_governor, target_governor):
        # This method is no longer needed as progress is handled in _update_ui
        pass

    def load_preferences(self):
        try:
            with open("scan_preferences.json", "r") as f:
                preferences = json.load(f)
            self.scan_options_frame.first_screen_options_frame.set(preferences)
            self.scan_options_frame.second_screen_options_frame.set(preferences)
            self.scan_options_frame.third_screen_options_frame.set(preferences)
        except FileNotFoundError:
            pass

    def closeEvent(self, event):
        if self.log_file:
            self.log_file.close()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    window.log_file = open(os.devnull, "w")
    sys.stdout = window.log_file
    sys.stderr = window.log_file
    sys.exit(app.exec())
