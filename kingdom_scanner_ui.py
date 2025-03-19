import logging
import os
import sys
import threading
import datetime
import time
import csv
from datetime import date
from io import TextIOWrapper
from pathlib import Path

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
    QComboBox, QGroupBox, QSpacerItem, QSizePolicy,
    QSystemTrayIcon, QListWidget, QSpinBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDateTime, QTime, QTimer, QSize
from PyQt6.QtGui import QIcon
import json
from roktracker.kingdom.additional_data import AdditionalData
from roktracker.kingdom.governor_data import GovernorData
from roktracker.kingdom.scanner import KingdomScanner
from roktracker.utils.adb import get_bluestacks_port
from roktracker.utils.exception_handling import GuiExceptionHandler
from roktracker.utils.validator import validate_installation, sanitize_scanname
from threading import Thread
from typing import Dict, List, Any

from roktracker.utils.database import HistoricalDatabase
from roktracker.utils.analytics import KingdomAnalytics
from roktracker.utils.analytics_export import AnalyticsExporter

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
            if isinstance(value["default"], bool):
                checkbox.setChecked(value["default"])
            elif callable(value["default"]):
                checkbox.setChecked(value["default"]())
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
        
        self.calendar = QCalendarWidget()
        self.calendar.setMinimumDate(date.today())
        layout.addWidget(self.calendar)
        
        time_frame = QFrame()
        time_layout = QHBoxLayout()
        time_frame.setLayout(time_layout)
        
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        time_layout.addWidget(QLabel("Time:"))
        time_layout.addWidget(self.time_edit)
        
        layout.addWidget(time_frame)
        
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
        
        self.countdown_label = QLabel()
        self.countdown_label.setStyleSheet("color: #2060c0; font-weight: bold;")
        self.countdown_label.hide()
        
        self.cancel_button = QPushButton("Cancel Schedule")
        self.cancel_button.clicked.connect(self.cancel_schedule)
        self.cancel_button.hide()
        
        layout.addWidget(QLabel("Schedule scan:"), 0, 0)
        layout.addWidget(self.schedule_switch, 0, 1)
        layout.addWidget(QLabel("Start time:"), 1, 0)
        layout.addWidget(self.time_button, 1, 1)
        layout.addWidget(self.countdown_label, 2, 0, 1, 2)
        layout.addWidget(self.cancel_button, 3, 0, 1, 2)
        
        self.scheduled_time = None
        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update_countdown)
        
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
                self.start_countdown()

    def toggle_schedule(self):
        is_checked = self.schedule_switch.isChecked()
        self.time_button.setEnabled(is_checked)
        if not is_checked:
            self.cancel_schedule()

    def start_countdown(self):
        if self.scheduled_time:
            self.countdown_label.show()
            self.cancel_button.show()
            self.update_countdown()
            self.countdown_timer.start(1000)

    def update_countdown(self):
        if self.scheduled_time:
            now = datetime.datetime.now()
            time_left = self.scheduled_time - now
            total_seconds = time_left.total_seconds()
            
            if total_seconds <= 0:
                self.countdown_timer.stop()
                self.countdown_label.hide()
                self.cancel_button.hide()
                parent = self
                while parent.parent():
                    parent = parent.parent()
                    if isinstance(parent, App):
                        Thread(target=parent.launch_scanner).start()
                        break
                self.schedule_switch.setChecked(False)
                return
                
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            seconds = int(total_seconds % 60)
            self.countdown_label.setText(f"Starting in: {hours:02d}:{minutes:02d}:{seconds:02d}")

    def cancel_schedule(self):
        self.scheduled_time = None
        self.time_button.setText("Set time")
        self.countdown_timer.stop()
        self.countdown_label.hide()
        self.cancel_button.hide()
        self.schedule_switch.setChecked(False)

    def get_scheduled_time(self):
        if self.schedule_switch.isChecked() and self.scheduled_time:
            return self.scheduled_time
        return None

class BasicOptionsFrame(QFrame):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setMinimumWidth(350)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(5, 5, 5, 5)
        self.setLayout(main_layout)

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

        self.check_ch_switch = QCheckBox("Check City Hall Level")
        if config["scan"].get("check_cityhall", False):
            self.check_ch_switch.setChecked(True)
        options_layout.addWidget(self.check_ch_switch, row + 1, 0)

        self.ch_level_label = QLabel("Minimum CH Level:")
        options_layout.addWidget(self.ch_level_label, row + 1, 1)
        self.ch_level_text = QLineEdit()
        self.ch_level_text.setText(str(config["scan"].get("min_ch_level", 25)))
        self.ch_level_text.setEnabled(self.check_ch_switch.isChecked())
        options_layout.addWidget(self.ch_level_text, row + 1, 2)

        self.check_ch_switch.stateChanged.connect(self.toggle_ch_level)

        main_layout.addWidget(options_group)

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

        schedule_group = QGroupBox("Schedule")
        schedule_layout = QVBoxLayout()
        schedule_group.setLayout(schedule_layout)
        self.schedule_frame = ScheduleFrame()
        schedule_layout.addWidget(self.schedule_frame)
        main_layout.addWidget(schedule_group)

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
            "check_ch": self.check_ch_switch.isChecked(),
            "min_ch_level": int(self.ch_level_text.text()) if self.check_ch_switch.isChecked() else 0,
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

        if self.check_ch_switch.isChecked():
            if not is_string_int(self.ch_level_text.text()):
                val_errors.append("City Hall level must be a number")
            else:
                ch_level = int(self.ch_level_text.text())
                if ch_level < 1 or ch_level > 25:
                    val_errors.append("City Hall level must be between 1 and 25")

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

    def toggle_ch_level(self):
        self.ch_level_text.setEnabled(self.check_ch_switch.isChecked())

class ScanOptionsFrame(QFrame):
    def __init__(self, values):
        super().__init__()
        self.setMinimumWidth(250)
        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)
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
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)
        self.setLayout(layout)
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

        time_label = QLabel("Current Time")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_label.setStyleSheet("font-weight: bold;")

        progress_label = QLabel("Progress")
        progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_label.setStyleSheet("font-weight: bold;")

        eta_label = QLabel("Estimated Time")
        eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eta_label.setStyleSheet("font-weight: bold;")

        layout.addWidget(time_label, 0, 0)
        layout.addWidget(progress_label, 0, 1)
        layout.addWidget(eta_label, 0, 2)

        layout.addWidget(self.last_time_var, 1, 0)
        layout.addWidget(self.gov_number_var, 1, 1)
        layout.addWidget(self.approx_time_remaining_var, 1, 2)
        
        layout.addWidget(self.govs_skipped_var, 2, 1)

    def set_var(self, key, value):
        if key in self.values:
            self.values[key].setText(value)

class LastGovernorInfo(QFrame):
    def __init__(self, values):
        super().__init__()
        self.setMinimumWidth(300)
        main_layout = QVBoxLayout()
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(5, 5, 5, 5)
        self.setLayout(main_layout)
        
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
                elif key == "City Hall" and str(value).isdigit():
                    self.variables[key].setText(f"CH {value}")
                else:
                    self.variables[key].setText(str(value))
            else:
                self.additional_stats.set_var(key, value)

class AnalyticsTab(QWidget):
    def __init__(self, db: HistoricalDatabase):
        super().__init__()
        self.db = db
        self.analytics = KingdomAnalytics(self.db)
        self.exporter = AnalyticsExporter(self.db, self.analytics)
        
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        
        export_group = QGroupBox("Export Options")
        button_layout = QHBoxLayout()
        export_group.setLayout(button_layout)
        
        export_kingdom_btn = QPushButton("Export Kingdom Report")
        export_kingdom_btn.clicked.connect(self.export_kingdom_report)
        export_kingdom_btn.setMinimumHeight(30)
        button_layout.addWidget(export_kingdom_btn)
        
        export_governor_btn = QPushButton("Export Governor Report")
        export_governor_btn.clicked.connect(self.export_governor_report)
        export_governor_btn.setMinimumHeight(30)
        button_layout.addWidget(export_governor_btn)
        
        main_layout.addWidget(export_group)
        
        analysis_tabs = QTabWidget()
        
        kingdom_tab = QWidget()
        kingdom_layout = QVBoxLayout()
        kingdom_tab.setLayout(kingdom_layout)
        
        kingdom_split = QHBoxLayout()
        
        left_column = QVBoxLayout()
        summary_group = QGroupBox("Kingdom Summary")
        summary_layout = QGridLayout()
        summary_group.setLayout(summary_layout)
        
        self.summary_labels = {}
        summary_metrics = [
            ('Active Governors:', 'active_governors'),
            ('Total Alliances:', 'total_alliances'),
            ('Average Power:', 'avg_power'),
            ('Power Change:', 'power_change'),
            ('Average Kill Points:', 'avg_killpoints'),
            ('KP Change:', 'kp_change'),
            ('Total T4/T5 Kills:', 'total_t4t5')
        ]
        
        for i, (label_text, key) in enumerate(summary_metrics):
            label = QLabel(label_text)
            label.setStyleSheet("font-weight: bold;")
            value = QLabel()
            value.setStyleSheet("color: #2060c0;")
            summary_layout.addWidget(label, i, 0)
            summary_layout.addWidget(value, i, 1)
            self.summary_labels[key] = value
            
        left_column.addWidget(summary_group)
        left_column.addStretch()
        
        right_column = QVBoxLayout()
        
        self.charts_tabs = QTabWidget()
        
        power_tab = QWidget()
        power_layout = QVBoxLayout()
        power_tab.setLayout(power_layout)
        self.power_canvas = None
        self.power_layout = power_layout
        self.charts_tabs.addTab(power_tab, "Power Analysis")
        
        kp_tab = QWidget()
        kp_layout = QVBoxLayout()
        kp_tab.setLayout(kp_layout)
        self.kp_canvas = None
        self.kp_layout = kp_layout
        self.charts_tabs.addTab(kp_tab, "Kill Points Analysis")
        
        kills_tab = QWidget()
        kills_layout = QVBoxLayout()
        kills_tab.setLayout(kills_layout)
        self.kills_canvas = None
        self.kills_layout = kills_layout
        self.charts_tabs.addTab(kills_tab, "T4/T5 Kills")
        
        alliance_tab = QWidget()
        alliance_layout = QVBoxLayout()
        alliance_tab.setLayout(alliance_layout)
        self.alliance_canvas = None
        self.alliance_layout = alliance_layout
        self.charts_tabs.addTab(alliance_tab, "Alliance Power")
        
        right_column.addWidget(self.charts_tabs)
        
        kingdom_split.addLayout(left_column, 1)
        kingdom_split.addLayout(right_column, 3)
        kingdom_layout.addLayout(kingdom_split)
        
        analysis_tabs.addTab(kingdom_tab, "Kingdom Overview")
        
        comparison_tab = QWidget()
        self.comparison_layout = QVBoxLayout()
        comparison_tab.setLayout(self.comparison_layout)
        
        selection_group = QGroupBox("Governor Selection")
        selection_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #cccccc;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        selection_layout = QVBoxLayout()
        selection_group.setLayout(selection_layout)
        
        input_area = QHBoxLayout()
        input_area.setSpacing(10)
        
        self.gov_input = QLineEdit()
        self.gov_input.setPlaceholderText("Enter Governor ID")
        self.gov_input.setMinimumHeight(35)
        self.gov_input.setStyleSheet("""
            QLineEdit {
                padding: 5px 10px;
                border: 1px solid #cccccc;
                border-radius: 4px;
            }
            QLineEdit:focus {
                border: 1px solid #2060c0;
            }
        """)
        
        add_gov_btn = QPushButton("Add Governor")
        add_gov_btn.setMinimumHeight(35)
        add_gov_btn.setStyleSheet("""
            QPushButton {
                background-color: #2060c0;
                color: white;
                border-radius: 4px;
                padding: 6px 15px;
            }
            QPushButton:hover {
                background-color: #1a4b99;
            }
            QPushButton:pressed {
                background-color: #154282;
            }
        """)
        add_gov_btn.clicked.connect(self.add_governor_to_comparison)
        
        input_area.addWidget(self.gov_input, 3)
        input_area.addWidget(add_gov_btn, 1)
        selection_layout.addLayout(input_area)
        
        self.gov_list = QListWidget()
        self.gov_list.setMinimumHeight(150)
        self.gov_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
                padding: 5px;
            }
            QListWidget::item {
                padding: 5px;
                border-bottom: 1px solid #eeeeee;
            }
            QListWidget::item:selected {
                background-color: #e6f0ff;
                color: #2060c0;
            }
        """)
        selection_layout.addWidget(self.gov_list)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        control_button_style = """
            QPushButton {
                background-color: #f5f5f5;
                border: 1px solid #cccccc;
                border-radius: 4px;
                padding: 6px 15px;
                color: #333333;
            }
            QPushButton:hover {
                background-color: #e6e6e6;
                border-color: #999999;
            }
            QPushButton:pressed {
                background-color: #d9d9d9;
            }
        """
        
        remove_gov_btn = QPushButton("Remove Selected")
        remove_gov_btn.setMinimumHeight(35)
        remove_gov_btn.setStyleSheet(control_button_style)
        remove_gov_btn.clicked.connect(self.remove_governor_from_comparison)
        
        clear_gov_btn = QPushButton("Clear All")
        clear_gov_btn.setMinimumHeight(35)
        clear_gov_btn.setStyleSheet(control_button_style)
        clear_gov_btn.clicked.connect(self.clear_comparison_list)
        
        compare_btn = QPushButton("Compare Governors")
        compare_btn.setMinimumHeight(35)
        compare_btn.setStyleSheet("""
            QPushButton {
                background-color: #2060c0;
                color: white;
                border-radius: 4px;
                padding: 6px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1a4b99;
            }
            QPushButton:pressed {
                background-color: #154282;
            }
        """)
        compare_btn.clicked.connect(self.update_comparison)
        
        button_layout.addWidget(remove_gov_btn)
        button_layout.addWidget(clear_gov_btn)
        button_layout.addWidget(compare_btn)
        selection_layout.addLayout(button_layout)
        
        self.comparison_layout.addWidget(selection_group)
        
        self.comparison_result_label = QLabel("Add governors and click Compare to see comparison")
        self.comparison_result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.comparison_result_label.setStyleSheet("color: #666666; padding: 15px;")
        self.comparison_layout.addWidget(self.comparison_result_label)
        
        analysis_tabs.addTab(comparison_tab, "Governor Comparison")

        prediction_tab = QWidget()
        self.prediction_layout = QVBoxLayout()
        prediction_tab.setLayout(self.prediction_layout)
        
        pred_group = QGroupBox("Prediction Settings")
        pred_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #cccccc;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        pred_layout = QVBoxLayout()
        pred_group.setLayout(pred_layout)
        
        input_grid = QGridLayout()
        input_grid.setSpacing(10)
        
        gov_id_label = QLabel("Governor ID:")
        gov_id_label.setStyleSheet("font-weight: normal;")
        self.pred_gov_input = QLineEdit()
        self.pred_gov_input.setPlaceholderText("Enter Governor ID")
        self.pred_gov_input.setMinimumHeight(35)
        self.pred_gov_input.setStyleSheet("""
            QLineEdit {
                padding: 5px 10px;
                border: 1px solid #cccccc;
                border-radius: 4px;
            }
            QLineEdit:focus {
                border: 1px solid #2060c0;
            }
        """)
        
        period_label = QLabel("Prediction Period:")
        period_label.setStyleSheet("font-weight: normal;")
        self.days_spinbox = QSpinBox()
        self.days_spinbox.setRange(7, 90)
        self.days_spinbox.setValue(30)
        self.days_spinbox.setPrefix("Predict ")
        self.days_spinbox.setSuffix(" days")
        self.days_spinbox.setMinimumHeight(35)
        self.days_spinbox.setStyleSheet("""
            QSpinBox {
                padding: 5px 10px;
                border: 1px solid #cccccc;
                border-radius: 4px;
            }
            QSpinBox:focus {
                border: 1px solid #2060c0;
            }
        """)
        
        input_grid.addWidget(gov_id_label, 0, 0)
        input_grid.addWidget(self.pred_gov_input, 0, 1)
        input_grid.addWidget(period_label, 1, 0)
        input_grid.addWidget(self.days_spinbox, 1, 1)
        
        pred_layout.addLayout(input_grid)
        
        predict_btn = QPushButton("Generate Prediction")
        predict_btn.setMinimumHeight(35)
        predict_btn.setStyleSheet("""
            QPushButton {
                background-color: #2060c0;
                color: white;
                border-radius: 4px;
                padding: 6px 15px;
                font-weight: bold;
                margin-top: 10px;
            }
            QPushButton:hover {
                background-color: #1a4b99;
            }
            QPushButton:pressed {
                background-color: #154282;
            }
        """)
        predict_btn.clicked.connect(self.update_prediction)
        pred_layout.addWidget(predict_btn)
        
        self.prediction_layout.addWidget(pred_group)
        
        instruction_label = QLabel("Enter a governor ID and select prediction period to generate forecast")
        instruction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instruction_label.setStyleSheet("""
            color: #666666;
            padding: 15px;
            font-style: italic;
        """)
        self.prediction_layout.addWidget(instruction_label)
        
        analysis_tabs.addTab(prediction_tab, "Predictive Analysis")
        
        main_layout.addWidget(analysis_tabs)
        
        refresh_btn = QPushButton("Refresh Analytics")
        refresh_btn.setMinimumHeight(35)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #2060c0;
                color: white;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: #1a4b99;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_analytics)
        main_layout.addWidget(refresh_btn)
        
        self.refresh_analytics()

    def clear_comparison_list(self):
        self.gov_list.clear()
        if hasattr(self, 'comparison_canvas'):
            self.comparison_canvas.setParent(None)
            delattr(self, 'comparison_canvas')

    def add_governor_to_comparison(self):
        gov_id = self.gov_input.text().strip()
        existing_ids = []
        for i in range(self.gov_list.count()):
            item = self.gov_list.item(i)
            if item is not None:
                existing_ids.append(item.text())
        
        if gov_id and gov_id not in existing_ids:
            self.gov_list.addItem(gov_id)
            self.gov_input.clear()

    def remove_governor_from_comparison(self):
        current_item = self.gov_list.currentItem()
        if current_item is not None:
            self.gov_list.takeItem(self.gov_list.row(current_item))

    def update_comparison(self):
        if self.gov_list.count() < 2:
            QMessageBox.warning(self, "Not Enough Governors", 
                              "Please add at least two governors to compare.")
            return
        
        governor_ids = []
        for i in range(self.gov_list.count()):
            item = self.gov_list.item(i)
            if item is not None:
                governor_ids.append(item.text())
        
        if not governor_ids:
            return
            
        try:
            canvas = self.analytics.create_governor_comparison_plot(governor_ids)
            if canvas is None:
                QMessageBox.warning(self, "Insufficient Data", 
                                  "Not enough historical data for comparison.")
                return
            
            df = self.analytics.compare_governors(governor_ids)
            if df.empty:
                QMessageBox.warning(self, "No Data", 
                                  "No comparison data available for the selected governors.")
                return
            
            results_group = QGroupBox("Comparison Results")
            results_layout = QGridLayout()
            results_group.setLayout(results_layout)
            
            headers = ['Name', 'Alliance', 'Current Power', 'Daily Growth', 'Predicted Growth']
            for i, header in enumerate(headers):
                label = QLabel(header)
                label.setStyleSheet("font-weight: bold;")
                results_layout.addWidget(label, 0, i)
            
            for idx, (_, gov_data) in enumerate(df.iterrows(), start=1):
                results_layout.addWidget(QLabel(str(gov_data.get('name', ''))), idx, 0)
                results_layout.addWidget(QLabel(str(gov_data.get('alliance', '') or '')), idx, 1)
                results_layout.addWidget(QLabel(f"{gov_data.get('current_power', 0)/1_000_000:.1f}M"), idx, 2)
                results_layout.addWidget(QLabel(f"{gov_data.get('daily_power_growth', 0)/1_000_000:+.2f}M/day"), idx, 3)
                
                predicted_growth = gov_data.get('predicted_power_growth')
                power_r2 = gov_data.get('power_r2')
                if predicted_growth is not None and power_r2 is not None:
                    predicted = f"{predicted_growth/1_000_000:+.2f}M/day (R²={power_r2:.2f})"
                else:
                    predicted = "Insufficient data"
                results_layout.addWidget(QLabel(predicted), idx, 4)
            
            while self.comparison_layout.count() > 1:
                item = self.comparison_layout.takeAt(1)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            
            self.comparison_canvas = canvas
            self.comparison_layout.addWidget(self.comparison_canvas)
            self.comparison_layout.addWidget(results_group)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create comparison: {str(e)}")
            return

    def update_prediction(self):
        gov_id = self.pred_gov_input.text().strip()
        if not gov_id:
            QMessageBox.warning(self, "No Governor Selected", 
                              "Please enter a governor ID first.")
            return
            
        days = self.days_spinbox.value()
        
        try:
            canvas = self.analytics.create_governor_prediction_plot(gov_id, days)
            
            if canvas is None:
                QMessageBox.warning(self, "Insufficient Data", 
                                  "Need at least 3 data points to generate predictions.")
                return
                
            while self.prediction_layout.count() > 2:
                item = self.prediction_layout.takeAt(2)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            
            self.prediction_canvas = canvas
            self.prediction_layout.addWidget(self.prediction_canvas)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create prediction: {str(e)}")

    def clear_layout(self, layout):
        """Safely clear all widgets from a layout"""
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
                elif item.layout() is not None:
                    self.clear_layout(item.layout())

    def refresh_analytics(self):
        """Refresh all analytics displays with advanced analysis"""
        try:
            summary = self.analytics.get_kingdom_summary()
            if summary:
                for key, label in self.summary_labels.items():
                    value = summary.get(key, '')
                    label.setText(str(value))
            else:
                for label in self.summary_labels.values():
                    label.setText("No data")

            if self.power_canvas is not None:
                self.power_layout.removeWidget(self.power_canvas)
                self.power_canvas.deleteLater()
            self.power_canvas = self.analytics.create_advanced_power_trend_plot()
            if self.power_canvas:
                self.power_layout.addWidget(self.power_canvas)

            if self.kp_canvas is not None:
                self.kp_layout.removeWidget(self.kp_canvas)
                self.kp_canvas.deleteLater()
            self.kp_canvas = self.analytics.create_advanced_killpoints_trend_plot()
            if self.kp_canvas:
                self.kp_layout.addWidget(self.kp_canvas)

            if self.kills_canvas is not None:
                self.kills_layout.removeWidget(self.kills_canvas)
                self.kills_canvas.deleteLater()
            self.kills_canvas = self.analytics.create_t4t5_kills_trend_plot()
            if self.kills_canvas:
                self.kills_layout.addWidget(self.kills_canvas)

            if self.alliance_canvas is not None:
                self.alliance_layout.removeWidget(self.alliance_canvas)
                self.alliance_canvas.deleteLater()
            self.alliance_canvas = self.analytics.create_alliance_power_distribution()
            if self.alliance_canvas:
                self.alliance_layout.addWidget(self.alliance_canvas)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to refresh analytics: {str(e)}")

    def export_kingdom_report(self):
        try:
            output_dir = Path(get_app_root()) / "reports"
            output_dir.mkdir(exist_ok=True)
            filename = self.exporter.export_kingdom_report(output_dir)
            QMessageBox.information(self, "Success", f"Kingdom report exported to:\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export kingdom report:\n{str(e)}")
    
    def export_governor_report(self):
        governor_ids = []
        for i in range(self.gov_list.count()):
            item = self.gov_list.item(i)
            if item is not None:
                governor_ids.append(item.text())
                
        if not governor_ids:
            QMessageBox.warning(self, "No Governors Selected", 
                              "Please add governors to the comparison list first")
            return
            
        try:
            output_dir = Path(get_app_root()) / "reports"
            output_dir.mkdir(exist_ok=True)
            filename = self.exporter.export_governor_report(governor_ids, output_dir)
            QMessageBox.information(self, "Success", f"Governor report exported to:\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export governor report:\n{str(e)}")

class App(QMainWindow):
    update_ui_signal = pyqtSignal(dict)
    update_state_signal = pyqtSignal(str)
    schedule_scan_signal = pyqtSignal(datetime.datetime)
    
    def __init__(self):
        super().__init__()
        self.update_ui_signal.connect(self._update_ui)
        self.update_state_signal.connect(self._update_state)
        self.schedule_scan_signal.connect(self._handle_scheduled_scan)
        
        self.log_file: TextIOWrapper | None = None

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("images/kingdom.png"))
        self.tray_icon.setToolTip("Kingdom Scanner")
        self.tray_icon.show()

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

        self.setWindowTitle("Kingdom Scanner")
        self.setGeometry(100, 100, 750, 500)

        self.db = HistoricalDatabase()
        self.analytics = KingdomAnalytics(self.db)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        scanner_widget = QWidget()
        scanner_layout = QHBoxLayout()
        scanner_layout.setSpacing(5)
        scanner_layout.setContentsMargins(5, 5, 5, 5)
        scanner_widget.setLayout(scanner_layout)

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

        center_panel = QWidget()
        center_layout = QVBoxLayout()
        center_panel.setLayout(center_layout)
        
        self.options_frame = BasicOptionsFrame(self.config)
        center_layout.addWidget(self.options_frame)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_panel.setLayout(right_layout)

        self.last_gov_frame = LastGovernorInfo(
            [
                {"name": "ID", "col": 0},
                {"name": "Name", "col": 0},
                {"name": "Power", "col": 0},
                {"name": "Killpoints", "col": 0},
                {"name": "City Hall", "col": 0},
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

        scanner_layout.addWidget(left_panel, 1)
        scanner_layout.addWidget(center_panel, 2)
        scanner_layout.addWidget(right_panel, 1)

        tabs.addTab(scanner_widget, "Scanner")
        
        analytics_tab = AnalyticsTab(self.db)
        tabs.addTab(analytics_tab, "Analytics")

        self.load_preferences()
        
    def ask_confirm(self, msg) -> bool:
        result = QMessageBox.question(self, "No Governor found", msg)
        return result == QMessageBox.StandardButton.Yes

    def close_program(self):
        self.close()

    def start_scan(self):
        try:
            scheduled_time = self.options_frame.schedule_frame.get_scheduled_time()
            if scheduled_time:
                wait_seconds = (scheduled_time - datetime.datetime.now()).total_seconds()
                if wait_seconds > 0:
                    self.start_scan_button.setEnabled(False)
                    self.schedule_scan_signal.emit(scheduled_time)
                    return
            Thread(target=self.launch_scanner).start()
        except Exception as e:
            logger.error(f"Error starting scan: {str(e)}")
            self.state_callback("Error starting scan")
            self.start_scan_button.setEnabled(True)
            self.end_scan_button.setEnabled(False)
            QMessageBox.critical(self, "Error", f"Failed to start scan: {str(e)}")

    def _handle_scheduled_scan(self, scheduled_time):
        try:
            self.state_callback(f"Waiting to start at {scheduled_time.strftime('%H:%M')}")
            wait_ms = int((scheduled_time - datetime.datetime.now()).total_seconds() * 1000)
            if wait_ms > 0:
                QTimer.singleShot(wait_ms, lambda: Thread(target=self.launch_scanner).start())
            else:
                self.state_callback("Scheduled time passed")
                self.start_scan_button.setEnabled(True)
        except Exception as e:
            logger.error(f"Error handling scheduled scan: {str(e)}")
            self.state_callback("Schedule error")
            self.start_scan_button.setEnabled(True)
            QMessageBox.critical(self, "Error", f"Failed to handle scheduled scan: {str(e)}")

    def launch_scanner(self):
        if not self.options_frame.options_valid():
            return

        self.start_scan_button.setEnabled(False)
        scan_options = self.scan_options_frame.get()
        basic_options = self.options_frame.get_options()

        logger.info(f"Scan options: {scan_options}")
        logger.info(f"Basic options: {basic_options}")
        logger.info(f"CH check enabled: {basic_options['check_ch']}, min level: {basic_options['min_ch_level']}")

        try:
            self.end_scan_button.setEnabled(True)
            self.end_scan_button.setText("End scan")

            self.kingdom_scanner = KingdomScanner(
                self.config, 
                scan_options,
                basic_options["port"]
            )
            
            self.kingdom_scanner.check_ch = basic_options["check_ch"]
            self.kingdom_scanner.min_ch_level = basic_options["min_ch_level"]
            
            self.config["scan"]["check_cityhall"] = basic_options["check_ch"]
            self.config["scan"]["min_ch_level"] = basic_options["min_ch_level"]
            
            self.kingdom_scanner.set_governor_callback(self.governor_callback)
            self.kingdom_scanner.set_state_callback(self.state_callback)
            self.kingdom_scanner.set_continue_handler(self.ask_confirm)
            
            self.update_ui_signal.emit({"uuid": self.kingdom_scanner.run_id})

            logger.info(f"Scan started at {datetime.datetime.now()}")
            self.kingdom_scanner.start_scan(
                basic_options["name"],
                basic_options["amount"],
                basic_options["resume"],
                basic_options["inactives"],
                basic_options["validate_kills"],
                basic_options["reconstruct"],
                basic_options["validate_power"],
                basic_options["power_threshold"],
                basic_options["formats"],
            )

            self.update_ui_signal.emit({
                "success": True,
                "message": "The scan has been completed successfully.",
                "enable_start": True,
                "disable_end": True
            })
            logger.info(f"Scan completed at {datetime.datetime.now()}")

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
                "   - Ensure kingdom rankings view is accessible\n"
                "   - Check your kingdom membership status\n"
                "   - Check your internet connection\n\n"
                "5. Tools/Environment:\n"
                "   - Verify platform-tools (adb.exe) exists in deps folder\n"
                "   - Check if running as administrator helps\n\n"
                "Error details: {error}"
            ).format(name=basic_options.get("name", ""), port=basic_options["port"], error=str(error))
            self.update_ui_signal.emit({
                "error": "ADB Connection Error",
                "message": error_msg,
                "enable_start": True,
                "disable_end": True
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
                "   - Check paths for kingdom scanner are configured\n"
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
                "   - Check kingdom-scanner.log for details\n\n"
                "Error details: {error}"
            ).format(error=str(error))
            self.update_ui_signal.emit({
                "error": "Configuration Error",
                "message": error_msg,
                "enable_start": True,
                "disable_end": True
            })
            self.state_callback("Not started - Config Error")

        except Exception as error:
            logger.error(f"Unexpected error at {datetime.datetime.now()}: {error}")
            error_msg = (
                "An unexpected error occurred. Please try:\n\n"
                "1. Restarting BlueStacks\n"
                "2. Verifying kingdom view is accessible\n"
                "3. Checking network connectivity\n"
                "4. Ensuring enough disk space\n"
                "5. Restarting the scanner\n"
                "6. Checking game server status\n\n"
                "Error details: {error}"
            ).format(error=str(error))
            self.update_ui_signal.emit({
                "error": "Unexpected Error",
                "message": error_msg,
                "enable_start": True,
                "disable_end": True
            })
            self.state_callback("Not started - Fatal Error")
        finally:
            if hasattr(self, 'kingdom_scanner'):
                try:
                    self.kingdom_scanner.adb_client.kill_adb()
                except:
                    pass

    def end_scan(self):
        if not hasattr(self, 'kingdom_scanner'):
            logger.warning("Attempted to end scan but scanner was not initialized")
            return
        self.kingdom_scanner.end_scan()
        self.end_scan_button.setEnabled(False)
        self.end_scan_button.setText("Abort after next governor")

    def governor_callback(self, gov_data: GovernorData, extra_data: AdditionalData):
        skipped_text = f"{extra_data.skipped_governors} skips"
        if extra_data.skipped_governors == 1:
            skipped_text = f"{extra_data.skipped_governors} skip"

        self.update_ui_signal.emit({
            "governor_data": {
                "ID": gov_data.id,
                "Name": gov_data.name,
                "Power": to_int_or(gov_data.power, "Unknown"),
                "Killpoints": to_int_or(gov_data.killpoints, "Unknown"),
                "Dead": to_int_or(gov_data.dead, "Unknown"),
                "City Hall": to_int_or(gov_data.city_hall, "Unknown"),
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

        self.db.save_scan_data(self.kingdom_scanner.run_id, gov_data.name, [gov_data])

    def _update_ui(self, data):
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
            
            if progress in [25, 50, 75, 100]:
                self.tray_icon.showMessage(
                    "Scan Progress",
                    f"Kingdom scan is {progress}% complete",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000
                )

        if "enable_start" in data:
            self.start_scan_button.setEnabled(True)
            
        if "disable_end" in data:
            self.end_scan_button.setEnabled(False)
            self.end_scan_button.setText("End scan")

        if "error" in data:
            self.show_notification(
                data["error"],
                data["message"],
                QSystemTrayIcon.MessageIcon.Critical
            )

        if "success" in data:
            self.show_notification(
                "Scan Complete", 
                data["message"],
                QSystemTrayIcon.MessageIcon.Information
            )

    def state_callback(self, state):
        self.update_state_signal.emit(state)

    def _update_state(self, state):
        self.last_gov_frame.current_state.setText(state)

    def update_progress(self, current_governor, target_governor):
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

    def show_notification(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information):
        """Show both a system notification and a message box"""
        self.tray_icon.showMessage(title, message, icon, 5000)

        if icon == QSystemTrayIcon.MessageIcon.Critical:
            QMessageBox.critical(self, title, message)
        else:
            QMessageBox.information(self, title, message)

    def closeEvent(self, event):
        if self.log_file:
            self.log_file.close()
        event.accept()

if __name__ == "__main__":
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
    if sys.stderr.encoding != 'utf-8':
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
    
    app = QApplication(sys.argv)
    
    try:
        print("Initializing ADB server...")
        from roktracker.utils.adb import AdvancedAdbClient
        adb_path = str(Path(get_app_root()) / "deps" / "platform-tools" / "adb.exe")
        
        os.system(f'"{adb_path}" kill-server')
        time.sleep(1)
        
        os.environ['ANDROID_ADB_SERVER_PORT'] = str(5037)
        os.system(f'"{adb_path}" start-server')
        print("ADB server initialized successfully")
        
    except Exception as e:
        print(f"Warning: Failed to initialize ADB server: {e}")
    
    window = App()
    window.show()
    
    window.log_file = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = window.log_file
    sys.stderr = window.log_file
    
    sys.exit(app.exec())
