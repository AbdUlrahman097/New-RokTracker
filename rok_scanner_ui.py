import logging
import sys
import os
import time
import datetime
import threading
import json
from pathlib import Path
from threading import Thread
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout,
    QSystemTrayIcon, QMessageBox, QLayout, QHBoxLayout, QPushButton, QGroupBox, QLabel, QProgressBar, QSizePolicy, QCheckBox,
    QDialog, QLineEdit, QSpinBox, QComboBox, QDialogButtonBox, QFileDialog, QToolBar, QGridLayout, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal, QMutex, QMutexLocker, QSize
from PyQt6.QtGui import QIcon, QAction

from dummy_root import get_app_root
from roktracker.utils.exception_handling import GuiExceptionHandler
from roktracker.utils.validator import validate_installation
from roktracker.utils.general import load_config
from roktracker.utils.exceptions import ConfigError, AdbError
from roktracker.utils.output_formats import OutputFormats
from roktracker.utils.check_python import check_py_version

from alliance_scanner_ui import BasicOptionsFame as AllianceOptions, LastBatchInfo as AllianceBatch
from honor_scanner_ui import BasicOptionsFame as HonorOptions, LastBatchInfo as HonorBatch
from kingdom_scanner_ui import BasicOptionsFrame as KingdomOptions, LastGovernorInfo as KingdomInfo
from seed_scanner_ui import BasicOptionsFame as SeedOptions, LastBatchInfo as SeedBatch

from roktracker.kingdom.scanner import KingdomScanner
from roktracker.alliance.scanner import AllianceScanner 
from roktracker.honor.scanner import HonorScanner
from roktracker.seed.scanner import SeedScanner

from dataclasses import dataclass
from typing import List, Optional
from multiprocessing import Pool, cpu_count
import functools
import hashlib
import pickle
from pathlib import Path

@dataclass
class ValidationError(Exception):
    message: str
    field: Optional[str] = None

class GameStateError(Exception):
    pass

class OCRError(Exception):
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(module)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def setup_scanner_logger(scanner_type: str):
    logger = logging.getLogger(scanner_type)
    logger.setLevel(logging.INFO)
    
    log_file = str(get_app_root() / f"{scanner_type}-scanner.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        "%(asctime)s %(module)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    return logger

kingdom_logger = setup_scanner_logger("kingdom")
alliance_logger = setup_scanner_logger("alliance")
honor_logger = setup_scanner_logger("honor")
seed_logger = setup_scanner_logger("seed")

logger = logging.getLogger(__name__)
logger.info("RoK Scanner starting up")

ex_handler = GuiExceptionHandler(logger)

sys.excepthook = ex_handler.handle_exception
threading.excepthook = ex_handler.handle_thread_exception

check_py_version((3, 11))

class ScannerTab(QWidget):
    """Base class for scanner tabs"""
    def __init__(self, config, options_class, info_class, scanner_class, govs_per_batch=5):
        super().__init__()
        self.config = config
        self.scanner = None
        self.scanner_thread = None
        self.ui_mutex = QMutex()
        self.scanner_class = scanner_class
        self.scan_option_checkboxes = {}
        self.process_pool = None
        self.cache_dir = Path(get_app_root()) / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        
        layout = QHBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)
        
        if scanner_class == KingdomScanner:
            left_panel = QWidget()
            left_layout = QVBoxLayout(left_panel)
            left_layout.setSpacing(5)
            left_layout.setContentsMargins(5, 5, 5, 5)
            
            scan_options_group = QGroupBox("Scan Options")
            scan_options_group.setStyleSheet("""
                QGroupBox {
                    font-weight: bold;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    margin-top: 1em;
                    padding: 5px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 8px;
                    padding: 0 3px;
                }
            """)
            
            options_layout = QVBoxLayout()
            scan_options_group.setLayout(options_layout)

            first_group = QGroupBox("Basic Information")
            first_layout = QVBoxLayout()
            first_layout.setSpacing(2)
            basic_options = {
                "ID": True,
                "Name": True,
                "Power": True,
                "Killpoints": True,
                "Alliance": True
            }
            for option, default_value in basic_options.items():
                checkbox = QCheckBox(option)
                checkbox.setChecked(default_value)
                first_layout.addWidget(checkbox)
                self.scan_option_checkboxes[option] = checkbox
            first_group.setLayout(first_layout)
            options_layout.addWidget(first_group)

            second_group = QGroupBox("Kill Statistics")
            second_layout = QVBoxLayout()
            second_layout.setSpacing(2)
            kills_options = {
                "T1 Kills": False,
                "T2 Kills": False,
                "T3 Kills": False,
                "T4 Kills": False,
                "T5 Kills": False,
                "Ranged": False
            }
            for option, default_value in kills_options.items():
                checkbox = QCheckBox(option)
                checkbox.setChecked(default_value)
                second_layout.addWidget(checkbox)
                self.scan_option_checkboxes[option] = checkbox
            second_group.setLayout(second_layout)
            options_layout.addWidget(second_group)

            third_group = QGroupBox("Additional Statistics")
            third_layout = QVBoxLayout()
            third_layout.setSpacing(2)
            other_options = {
                "Deads": False,
                "Rss Assistance": False,
                "Rss Gathered": False,
                "Helps": False
            }
            for option, default_value in other_options.items():
                checkbox = QCheckBox(option)
                checkbox.setChecked(default_value)
                third_layout.addWidget(checkbox)
                self.scan_option_checkboxes[option] = checkbox
            third_group.setLayout(third_layout)
            options_layout.addWidget(third_group)
            left_layout.addWidget(scan_options_group)

            presets_layout = QHBoxLayout()
            presets_layout.setSpacing(4)
            
            preset_button_style = """
                QPushButton {
                    padding: 4px 12px;
                    border-radius: 3px;
                    font-weight: bold;
                    background-color: #f0f0f0;
                    border: 1px solid #cccccc;
                    color: #333333;
                }
                QPushButton:hover {
                    background-color: #e0e0e0;
                }
                QPushButton:pressed {
                    background-color: #d0d0d0;
                }
            """
            
            full_scan_btn = QPushButton("Full Scan")
            full_scan_btn.setStyleSheet(preset_button_style)
            full_scan_btn.clicked.connect(lambda: self.apply_scan_preset("full"))
            
            seed_scan_btn = QPushButton("Seed Scan")
            seed_scan_btn.setStyleSheet(preset_button_style)
            seed_scan_btn.clicked.connect(lambda: self.apply_scan_preset("seed"))
            
            custom_scan_btn = QPushButton("Clear All")
            custom_scan_btn.setStyleSheet(preset_button_style)
            custom_scan_btn.clicked.connect(lambda: self.apply_scan_preset("custom"))
            
            presets_layout.addWidget(full_scan_btn)
            presets_layout.addWidget(seed_scan_btn)
            presets_layout.addWidget(custom_scan_btn)
            left_layout.addLayout(presets_layout)

            controls_group = QGroupBox("Scan Controls")
            controls_layout = QHBoxLayout()
            controls_group.setLayout(controls_layout)

            self.start_scan_button = QPushButton("Start Scan")
            self.start_scan_button.setMinimumHeight(40)
            self.start_scan_button.clicked.connect(self.start_scan)
            controls_layout.addWidget(self.start_scan_button)

            self.end_scan_button = QPushButton("End Scan")
            self.end_scan_button.setMinimumHeight(40)
            self.end_scan_button.clicked.connect(self.end_scan)
            controls_layout.addWidget(self.end_scan_button)
            
            left_layout.addWidget(controls_group)
            left_layout.addStretch()

            center_panel = QWidget()
            center_layout = QVBoxLayout(center_panel)
            center_layout.setSpacing(5)
            
            self.options_frame = options_class(config)
            center_layout.addWidget(self.options_frame)
            center_layout.addStretch()

            right_panel = QWidget()
            right_layout = QVBoxLayout(right_panel)
            right_layout.setSpacing(5)
            
            self.info_frame = info_class([
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
            ])
            right_layout.addWidget(self.info_frame)

            left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            center_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            right_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

            layout.addWidget(left_panel, 1)
            layout.addWidget(center_panel, 2)
            layout.addWidget(right_panel, 1)

        else:
            left_panel = QWidget()
            left_layout = QVBoxLayout(left_panel)
            left_layout.setSpacing(8)
            
            self.options_frame = options_class(self, config)
            left_layout.addWidget(self.options_frame)
            
            controls_group = QGroupBox("Scan Controls")
            controls_layout = QHBoxLayout()
            controls_group.setLayout(controls_layout)

            self.start_scan_button = QPushButton("Start Scan")
            self.start_scan_button.setMinimumHeight(40)
            self.start_scan_button.clicked.connect(self.start_scan)
            controls_layout.addWidget(self.start_scan_button)

            self.end_scan_button = QPushButton("End Scan")
            self.end_scan_button.setMinimumHeight(40)
            self.end_scan_button.clicked.connect(self.end_scan)
            controls_layout.addWidget(self.end_scan_button)
            
            left_layout.addWidget(controls_group)
            left_layout.addStretch()

            right_container = QWidget()
            right_layout = QVBoxLayout(right_container)
            right_layout.setSpacing(8)

            status_container = QWidget()
            status_layout = QVBoxLayout(status_container)
            status_layout.setSpacing(8)

            self.info_frame = info_class(self, govs_per_batch)
            status_layout.addWidget(self.info_frame)
            
            progress_group = QGroupBox("Scan Progress")
            progress_inner_layout = QVBoxLayout()
            progress_inner_layout.setSpacing(5)
            progress_group.setLayout(progress_inner_layout)

            self.current_state = QLabel("Not started")
            progress_inner_layout.addWidget(self.current_state)

            self.progress_bar = QProgressBar()
            self.progress_bar.setMinimumHeight(24)
            progress_inner_layout.addWidget(self.progress_bar)
            status_layout.addWidget(progress_group)

            right_layout.addWidget(status_container)
            right_layout.addStretch()
            
            left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            right_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            
            layout.addWidget(left_panel, 4)
            layout.addWidget(right_container, 6)

    def start_scan(self):
        """Start scan in a new thread"""
        with QMutexLocker(self.ui_mutex):
            if self.scanner_thread and self.scanner_thread.is_alive():
                return

            if not self.options_frame.options_valid():
                return
                
            self.scanner_thread = Thread(
                target=self.launch_scanner,
                name="ScannerThread",
                daemon=True
            )
            self.scanner_thread.start()

    def launch_scanner(self):
        """Initialize and launch the scanner"""
        try:
            options = self.options_frame.get_options()
            
            if self.config.get("performance", {}).get("cache_enabled", True):
                try:
                    if self.load_from_cache(options):
                        return
                except Exception as e:
                    logger.warning(f"Cache loading failed: {e}")
            
            if self.scanner_class == KingdomScanner:
                scan_options = {opt: checkbox.isChecked() 
                              for opt, checkbox in self.scan_option_checkboxes.items()}
                
                scan_options.update({
                    "check_ch": options.get("check_ch", False),
                    "min_ch_level": options.get("min_ch_level", 25),
                    "inactives": options.get("inactives", False),
                    "validate_kills": options.get("validate_kills", True),
                    "reconstruct": options.get("reconstruct", False),
                    "validate_power": options.get("validate_power", True),
                    "power_threshold": options.get("power_threshold", 0),
                    "resume": options.get("resume", False),
                    "parallel_enabled": self.config.get("performance", {}).get("parallel_enabled", True),
                    "worker_threads": self.config.get("performance", {}).get("worker_threads", cpu_count() - 1),
                    "image_optimization": self.config.get("performance", {}).get("image_optimization", True),
                    "image_quality": self.config.get("performance", {}).get("image_quality", 85)
                })
                
                if not any(scan_options.values()):
                    raise ValidationError("No scan options are selected.", "scan_options")

                self.scanner = self.scanner_class(self.config, scan_options, options["port"])
            else:
                self.scanner = self.scanner_class(options["port"], self.config)
                
                if hasattr(self.scanner, 'set_performance_options'):
                    self.scanner.set_performance_options({
                        "parallel_enabled": self.config.get("performance", {}).get("parallel_enabled", True),
                        "worker_threads": self.config.get("performance", {}).get("worker_threads", cpu_count() - 1),
                        "image_optimization": self.config.get("performance", {}).get("image_optimization", True),
                        "image_quality": self.config.get("performance", {}).get("image_quality", 85)
                    })

            self.scanner.set_state_callback(self.state_callback)
            if hasattr(self.scanner, 'set_batch_callback'):
                self.scanner.set_batch_callback(self.governor_callback)
            elif hasattr(self.scanner, 'set_governor_callback'):
                self.scanner.set_governor_callback(self.governor_callback)

            if hasattr(self.scanner, 'run_id') and hasattr(self.options_frame, 'set_uuid'):
                self.options_frame.set_uuid(self.scanner.run_id)

            self.start_scan_button.setEnabled(False)
            logger.info(f"Scan started at {datetime.datetime.now()}")

            if self.scanner_class == KingdomScanner:
                self.scanner.start_scan(
                    options["name"],
                    options["amount"],
                    scan_options["resume"],
                    scan_options["inactives"],
                    scan_options["validate_kills"],
                    scan_options["reconstruct"],
                    scan_options["validate_power"],
                    scan_options["power_threshold"],
                    options["formats"]
                )
            else:
                self.scanner.start_scan(
                    options["name"],
                    options["amount"],
                    options["formats"]
                )

            if self.config.get("performance", {}).get("cache_enabled", True):
                try:
                    self.save_to_cache(options)
                except Exception as e:
                    logger.warning(f"Failed to cache results: {e}")

        except Exception as error:
            self.handle_scan_error(error)
        finally:
            self.cleanup_scan()

    def load_from_cache(self, options):
        """Try to load and use cached scan results"""
        cache_key = hashlib.md5(json.dumps(options, sort_keys=True).encode()).hexdigest()
        cache_file = self.cache_dir / f"{cache_key}.cache"
        
        if cache_file.exists():
            cache_age = time.time() - cache_file.stat().st_mtime
            max_age = self.config.get("performance", {}).get("cache_lifetime", 24) * 3600
            
            if cache_age < max_age:
                with open(cache_file, 'rb') as f:
                    cached_data = pickle.load(f)
                    self.process_cached_results(cached_data)
                    return True
        return False

    def save_to_cache(self, options):
        """Save scan results to cache"""
        if hasattr(self, 'scanner') and self.scanner and hasattr(self.scanner, 'get_results'):
            cache_key = hashlib.md5(json.dumps(options, sort_keys=True).encode()).hexdigest()
            cache_file = self.cache_dir / f"{cache_key}.cache"
            
            scan_results = self.scanner.get_results()
            with open(cache_file, 'wb') as f:
                pickle.dump(scan_results, f)

    def cleanup_scan(self):
        """Clean up resources after scan completion"""
        try:
            if self.process_pool:
                self.process_pool.terminate()
                self.process_pool = None
                
            if hasattr(self, 'scanner') and self.scanner:
                self.scanner.end_scan()
        except Exception as e:
            logger.error(f"Error cleaning up scanner: {e}")
        finally:
            self.start_scan_button.setEnabled(True)
            self.end_scan_button.setEnabled(True)
            self.end_scan_button.setText("End Scan")

    def handle_scan_error(self, error):
        """Handle various scan errors with appropriate messages"""
        if isinstance(error, ValidationError):
            logger.error(f"Validation error: {error}")
            self.state_callback("Not started - Validation Error")
            error_msg = str(error)
            if error.field == "scan_options":
                error_msg = "Please select at least one scan option before starting the scan."
            QMessageBox.warning(self, "Validation Error", error_msg)
            
        elif isinstance(error, AdbError):
            logger.error(f"ADB connection error: {error}")
            self.state_callback("Not started - ADB Error")
            error_msg = self.get_adb_error_message(error)
            QMessageBox.critical(self, "ADB Error", error_msg)
            
        elif isinstance(error, OCRError):
            logger.error(f"OCR error: {error}")
            self.state_callback("Error - OCR Failed")
            error_msg = self.get_ocr_error_message(error)
            QMessageBox.warning(self, "OCR Error", error_msg)
            
        else:
            logger.error(f"Unexpected error during scan: {error}", exc_info=True)
            self.state_callback("Error during scan")
            error_msg = self.get_generic_error_message(error)
            QMessageBox.critical(self, "Error", error_msg)

    def get_adb_error_message(self, error):
        """Get detailed ADB error message"""
        return (
            f"Failed to connect to emulator.\n\n"
            f"Details: {error}\n\n"
            f"Troubleshooting:\n"
            f"1. Verify emulator is running\n"
            f"2. Check port number\n"
            f"3. Try restarting emulator\n"
            f"4. Run as administrator"
        )

    def get_ocr_error_message(self, error):
        """Get detailed OCR error message"""
        return (
            f"Text recognition error.\n\n"
            f"Details: {error}\n\n"
            f"Try the following:\n"
            f"1. Check tessdata installation\n"
            f"2. Adjust window size/resolution\n"
            f"3. Ensure UI is not obscured\n"
            f"4. Verify game language settings"
        )

    def get_generic_error_message(self, error):
        """Get generic error message"""
        return (
            f"An unexpected error occurred.\n\n"
            f"Details: {str(error)}\n\n"
            f"Please try:\n"
            f"1. Restarting the application\n"
            f"2. Checking logs for details\n"
            f"3. Verifying system requirements\n"
            f"4. Running as administrator"
        )

    def end_scan(self):
        """Handle scan termination"""
        with QMutexLocker(self.ui_mutex):
            if self.scanner:
                self.scanner.end_scan()
                self.end_scan_button.setEnabled(False)
                self.end_scan_button.setText("Aborting...")
                logger.info("Scan abort requested")

    def state_callback(self, state):
        """Update state label"""
        self.current_state.setText(state)

    def governor_callback(self, gov_data, extra_data):
        """Update progress and governor info"""
        if hasattr(self.info_frame, 'set'):
            self.info_frame.set(gov_data)
        
        if extra_data and hasattr(extra_data, 'current') and hasattr(extra_data, 'total'):
            progress = int((extra_data.current / extra_data.total) * 100)
            self.progress_bar.setValue(progress)

    def apply_scan_preset(self, preset: str):
        """Apply a scan preset configuration"""
        if not hasattr(self, 'scan_option_checkboxes'):
            return
            
        if preset == "full":
            for checkbox in self.scan_option_checkboxes.values():
                checkbox.setChecked(True)
        elif preset == "seed":
            seed_options = {"ID", "Name", "Power", "Killpoints", "Alliance"}
            for option, checkbox in self.scan_option_checkboxes.items():
                checkbox.setChecked(option in seed_options)
        else:
            for checkbox in self.scan_option_checkboxes.values():
                checkbox.setChecked(False)

    def process_cached_results(self, cached_data):
        """Process and display cached scan results"""
        try:
            self.start_scan_button.setEnabled(False)
            self.state_callback("Loading cached results...")
            
            for item in cached_data:
                self.governor_callback(item, None)
            
            self.state_callback("Completed (from cache)")
            self.start_scan_button.setEnabled(True)
            
        except Exception as e:
            logger.error(f"Error processing cached results: {e}")
            self.state_callback("Cache processing error")
            self.start_scan_button.setEnabled(True)

class UnifiedScannerApp(QMainWindow):
    update_ui_signal = pyqtSignal(dict)
    update_status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        self.update_ui_signal.connect(self._update_ui)
        self.update_status_signal.connect(self._update_status)
        
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("images/kingdom.png"))
        self.tray_icon.setToolTip("RoK Scanner")
        self.tray_icon.show()

        self.tabs = None

        file_validation = validate_installation()
        if not file_validation.success:
            QMessageBox.critical(self, "Validation failed", "\n".join(file_validation.messages))
            self.close()
            return

        try:
            self.config = load_config()
        except ConfigError as e:
            logger.fatal(str(e))
            QMessageBox.critical(self, "Invalid Config", str(e))
            self.close()
            return

        self.setup_ui()

    def setup_ui(self):
        """Initialize the UI components"""
        self.setWindowTitle("RoK Scanner")
        self.setMinimumSize(600, 740)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)
        
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.show_settings)
        toolbar.addAction(settings_action)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background: #f5f5f5;
            }
            QTabWidget::tab-bar {
                alignment: center;
            }
            QTabBar::tab {
                min-width: 100px;
                max-width: 200px;
                padding: 6px 12px;
                margin: 0px 1px 0px 0px;
                background: #e6e6e6;
                border: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-weight: bold;
                color: #666666;
            }
            QTabBar::tab:selected {
                color: #ffffff;
                margin-bottom: -1px;
            }
            QTabBar::tab:hover:!selected {
                background: #d0d0d0;
            }
            QTabBar::tab:selected:first {
                background: #2060c0;
            }
            QTabBar::tab:selected:nth-child(2) {
                background: #20a040;
            }
            QTabBar::tab:selected:nth-child(3) {
                background: #c02040;
            }
            QTabBar::tab:selected:last {
                background: #8040c0;
            }
            /* Responsive adjustments */
            QTabBar::tab:!selected {
                margin-top: 2px;
            }
            @media (max-width: 800px) {
                QTabBar::tab {
                    min-width: 80px;
                    padding: 4px 8px;
                }
            }
        """)
        
        kingdom_tab = ScannerTab(self.config, KingdomOptions, KingdomInfo, KingdomScanner)
        alliance_tab = ScannerTab(self.config, AllianceOptions, AllianceBatch, AllianceScanner, 6)
        honor_tab = ScannerTab(self.config, HonorOptions, HonorBatch, HonorScanner, 5)
        seed_tab = ScannerTab(self.config, SeedOptions, SeedBatch, SeedScanner, 6)

        self.tabs.addTab(kingdom_tab, QIcon("images/kingdom.png"), "Kingdom")
        self.tabs.addTab(alliance_tab, QIcon("images/alliance.png"), "Alliance")
        self.tabs.addTab(honor_tab, QIcon("images/honor.png"), "Honor")
        self.tabs.addTab(seed_tab, QIcon("images/seed.png"), "Seed")

        self.tabs.currentChanged.connect(self.update_tray_icon)

        layout.addWidget(self.tabs)

    def update_tray_icon(self, index):
        """Update tray icon based on selected tab"""
        icons = ["kingdom.png", "alliance.png", "honor.png", "seed.png"]
        if 0 <= index < len(icons):
            self.tray_icon.setIcon(QIcon(f"images/{icons[index]}"))

    def show_settings(self):
        """Show the settings dialog"""
        try:
            dialog = SettingsDialog(self.config, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    self.config = load_config()
                    if self.tabs:
                        for i in range(self.tabs.count()):
                            tab = self.tabs.widget(i)
                            if isinstance(tab, ScannerTab):
                                tab.config = self.config
                except ConfigError as e:
                    logger.error(f"Failed to reload config: {e}")
                    QMessageBox.critical(self, "Error", "Failed to reload settings. Please restart the application.")
        except Exception as e:
            logger.error(f"Error showing settings dialog: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open settings: {e}")

    def _update_ui(self, data: dict):
        """Handle UI updates in a thread-safe way"""
        if not self.tabs:
            return
            
        current_tab = self.tabs.currentWidget()
        if isinstance(current_tab, ScannerTab):
            if "error" in data:
                self.show_notification(
                    data["error"],
                    data["message"],
                    QSystemTrayIcon.MessageIcon.Critical
                )
            elif "success" in data:
                self.show_notification(
                    "Scan Complete",
                    data["message"],
                    QSystemTrayIcon.MessageIcon.Information
                )
            elif "progress" in data and hasattr(current_tab, 'progress_bar'):
                progress = int((data["progress"]["current"] / data["progress"]["total"]) * 100)
                current_tab.progress_bar.setValue(progress)
                
                if progress in [25, 50, 75, 100] and self.tabs:
                    try:
                        tab_name = self.tabs.tabText(self.tabs.currentIndex())
                        self.tray_icon.showMessage(
                            "Scan Progress",
                            f"{tab_name} scan is {progress}% complete",
                            QSystemTrayIcon.MessageIcon.Information,
                            3000
                        )
                    except Exception as e:
                        logger.warning(f"Failed to show progress notification: {e}")

    def _update_status(self, status: str):
        """Update status label in a thread-safe way"""
        if not self.tabs:
            return
            
        current_tab = self.tabs.currentWidget()
        if isinstance(current_tab, ScannerTab) and hasattr(current_tab, 'current_state'):
            current_tab.current_state.setText(status)

    def show_notification(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information):
        """Show both a system notification and a message box"""
        self.tray_icon.showMessage(title, message, icon, 5000)
        
        if icon == QSystemTrayIcon.MessageIcon.Critical:
            QMessageBox.critical(self, title, message)
        else:
            QMessageBox.information(self, title, message)

    def closeEvent(self, event):
        """Handle application closing"""
        if self.tabs:
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if isinstance(tab, ScannerTab):
                    if hasattr(tab.options_frame, 'closeEvent'):
                        try:
                            tab.options_frame.closeEvent(event)
                        except Exception as e:
                            logger.error(f"Error closing options frame {i}: {str(e)}")
                    if hasattr(tab.info_frame, 'closeEvent'):
                        try:
                            tab.info_frame.closeEvent(event)
                        except Exception as e:
                            logger.error(f"Error closing info frame {i}: {str(e)}")

        super().closeEvent(event)

class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(400, 300)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        tab_widget = QTabWidget()
        tab_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_layout.setContentsMargins(6, 6, 6, 6)
        general_layout.setSpacing(6)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(10)
        
        emulator_group = QGroupBox("Emulator Settings")
        emulator_layout = QGridLayout()
        emulator_layout.setColumnStretch(1, 1)
        
        emulator_label = QLabel("Emulator:")
        self.emulator_combo = QComboBox()
        self.emulator_combo.addItems(["BlueStacks", "LDPlayer", "Nox"])
        self.emulator_combo.setCurrentText(config["general"]["emulator"])
        
        port_label = QLabel("ADB Port:")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(config["general"]["adb_port"])
        
        bs_path_label = QLabel("BlueStacks Config:")
        self.bs_path_edit = QLineEdit()
        self.bs_path_edit.setText(config["general"]["bluestacks"]["config"])
        bs_path_browse = QPushButton("Browse...")
        bs_path_browse.clicked.connect(self.browse_bs_config)
        
        emulator_layout.addWidget(emulator_label, 0, 0)
        emulator_layout.addWidget(self.emulator_combo, 0, 1, 1, 2)
        emulator_layout.addWidget(port_label, 1, 0)
        emulator_layout.addWidget(self.port_spin, 1, 1, 1, 2)
        emulator_layout.addWidget(bs_path_label, 2, 0)
        emulator_layout.addWidget(self.bs_path_edit, 2, 1)
        emulator_layout.addWidget(bs_path_browse, 2, 2)
        
        bs_name_label = QLabel("BlueStacks Name:")
        self.bs_name_edit = QLineEdit()
        self.bs_name_edit.setText(config["general"]["bluestacks"]["name"])
        
        emulator_layout.addWidget(bs_name_label, 3, 0)
        emulator_layout.addWidget(self.bs_name_edit, 3, 1, 1, 2)
        
        emulator_group.setLayout(emulator_layout)
        scroll_layout.addWidget(emulator_group)

        scan_group = QGroupBox("Scan Settings")
        scan_layout = QGridLayout()
        scan_layout.setColumnStretch(1, 1)
        
        row = 0
        self.resume_scan = QCheckBox("Resume scan")
        self.resume_scan.setChecked(config["scan"].get("resume", False))
        scan_layout.addWidget(self.resume_scan, row, 0, 1, 2)
        
        row += 1
        self.advanced_scroll = QCheckBox("Enable advanced scrolling")
        self.advanced_scroll.setChecked(config["scan"].get("advanced_scroll", True))
        scan_layout.addWidget(self.advanced_scroll, row, 0, 1, 2)
        
        row += 1
        self.track_inactives = QCheckBox("Track inactive players")
        self.track_inactives.setChecked(config["scan"].get("track_inactives", False))
        scan_layout.addWidget(self.track_inactives, row, 0, 1, 2)

        row += 1
        self.validate_power = QCheckBox("Validate power")
        self.validate_power.setChecked(config["scan"].get("validate_power", False))
        scan_layout.addWidget(self.validate_power, row, 0, 1, 2)
        
        row += 1
        power_threshold_label = QLabel("Power threshold:")
        self.power_threshold = QSpinBox()
        self.power_threshold.setRange(0, 1000000000)
        self.power_threshold.setValue(config["scan"].get("power_threshold", 100000))
        self.power_threshold.setSingleStep(100000)
        scan_layout.addWidget(power_threshold_label, row, 0)
        scan_layout.addWidget(self.power_threshold, row, 1)

        row += 1
        self.validate_kills = QCheckBox("Validate kills")
        self.validate_kills.setChecked(config["scan"].get("validate_kills", True))
        scan_layout.addWidget(self.validate_kills, row, 0, 1, 2)
        
        row += 1
        self.reconstruct_kills = QCheckBox("Reconstruct kills")
        self.reconstruct_kills.setChecked(config["scan"].get("reconstruct_kills", True))
        scan_layout.addWidget(self.reconstruct_kills, row, 0, 1, 2)

        row += 1
        self.check_cityhall = QCheckBox("Check city hall level")
        self.check_cityhall.setChecked(config["scan"].get("check_cityhall", False))
        scan_layout.addWidget(self.check_cityhall, row, 0, 1, 2)
        
        row += 1
        ch_level_label = QLabel("Minimum city hall level:")
        self.min_ch_level = QSpinBox()
        self.min_ch_level.setRange(1, 25)
        self.min_ch_level.setValue(config["scan"].get("min_ch_level", 25))
        scan_layout.addWidget(ch_level_label, row, 0)
        scan_layout.addWidget(self.min_ch_level, row, 1)
        
        scan_group.setLayout(scan_layout)
        scroll_layout.addWidget(scan_group)

        output_group = QGroupBox("Output Settings")
        output_layout = QVBoxLayout()
        
        format_label = QLabel("Output Formats:")
        output_layout.addWidget(format_label)
        
        self.format_checks = {}
        for fmt in ["xlsx", "csv", "jsonl"]:
            checkbox = QCheckBox(fmt.upper())
            checkbox.setChecked(config["scan"]["formats"].get(fmt, False))
            self.format_checks[fmt] = checkbox
            output_layout.addWidget(checkbox)
        
        output_group.setLayout(output_layout)
        scroll_layout.addWidget(output_group)

        timing_group = QGroupBox("Timing Settings")
        timing_layout = QGridLayout()
        timing_layout.setColumnStretch(1, 1)
        
        timings = config["scan"]["timings"]
        self.timing_spins = {}
        row = 0
        for timing_key, default_value in timings.items():
            label = QLabel(f"{timing_key.replace('_', ' ').title()}:")
            spin = QSpinBox()
            spin.setRange(1, 100)
            spin.setValue(int(default_value * 10))
            self.timing_spins[timing_key] = spin
            timing_layout.addWidget(label, row, 0)
            timing_layout.addWidget(spin, row, 1)
            timing_layout.addWidget(QLabel("/ 10 seconds"), row, 2)
            row += 1
        
        timing_group.setLayout(timing_layout)
        scroll_layout.addWidget(timing_group)
        
        scroll_layout.addStretch()
        
        scroll_area.setWidget(scroll_content)
        general_layout.addWidget(scroll_area)
        
        tab_widget.addTab(general_tab, "General")
        
        perf_tab = QWidget()
        perf_layout = QVBoxLayout(perf_tab)
        
        parallel_group = QGroupBox("Parallel Processing")
        parallel_layout = QVBoxLayout()
        
        self.enable_parallel = QCheckBox("Enable parallel processing")
        self.enable_parallel.setChecked(config.get("performance", {}).get("parallel_enabled", True))
        parallel_layout.addWidget(self.enable_parallel)
        
        threads_row = QHBoxLayout()
        threads_label = QLabel("Worker threads:")
        self.thread_count = QSpinBox()
        self.thread_count.setRange(1, cpu_count())
        self.thread_count.setValue(config.get("performance", {}).get("worker_threads", cpu_count() - 1))
        threads_row.addWidget(threads_label)
        threads_row.addWidget(self.thread_count)
        parallel_layout.addLayout(threads_row)
        
        parallel_group.setLayout(parallel_layout)
        perf_layout.addWidget(parallel_group)
        
        cache_group = QGroupBox("Scan Cache")
        cache_layout = QVBoxLayout()
        
        self.enable_cache = QCheckBox("Enable scan result caching")
        self.enable_cache.setChecked(config.get("performance", {}).get("cache_enabled", True))
        cache_layout.addWidget(self.enable_cache)
        
        cache_time_row = QHBoxLayout()
        cache_time_label = QLabel("Cache lifetime (hours):")
        self.cache_lifetime = QSpinBox()
        self.cache_lifetime.setRange(1, 168)
        self.cache_lifetime.setValue(config.get("performance", {}).get("cache_lifetime", 24))
        cache_time_row.addWidget(cache_time_label)
        cache_time_row.addWidget(self.cache_lifetime)
        cache_layout.addLayout(cache_time_row)
        
        cache_group.setLayout(cache_layout)
        perf_layout.addWidget(cache_group)
        
        image_group = QGroupBox("Image Processing")
        image_layout = QVBoxLayout()
        
        self.enable_optimization = QCheckBox("Enable image optimization")
        self.enable_optimization.setChecked(config.get("performance", {}).get("image_optimization", True))
        image_layout.addWidget(self.enable_optimization)
        
        quality_row = QHBoxLayout()
        quality_label = QLabel("Image quality:")
        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 100)
        self.image_quality.setValue(config.get("performance", {}).get("image_quality", 85))
        quality_row.addWidget(quality_label)
        quality_row.addWidget(self.image_quality)
        image_layout.addLayout(quality_row)
        
        image_group.setLayout(image_layout)
        perf_layout.addWidget(image_group)
        
        perf_layout.addStretch()
        tab_widget.addTab(perf_tab, "Performance")
        
        layout.addWidget(tab_widget)
        
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(button_box)
        
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.resize(500, 400)
    
    def browse_bs_config(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select BlueStacks Config File",
            "",
            "Config Files (*.conf);;All Files (*.*)"
        )
        if file_name:
            self.bs_path_edit.setText(file_name)
    
    def accept(self):
        self.config["general"]["emulator"] = self.emulator_combo.currentText()
        self.config["general"]["adb_port"] = self.port_spin.value()
        self.config["general"]["bluestacks"]["name"] = self.bs_name_edit.text()
        self.config["general"]["bluestacks"]["config"] = self.bs_path_edit.text()
        
        for fmt, checkbox in self.format_checks.items():
            self.config["scan"]["formats"][fmt] = checkbox.isChecked()
        
        self.config["scan"].update({
            "resume": self.resume_scan.isChecked(),
            "advanced_scroll": self.advanced_scroll.isChecked(),
            "track_inactives": self.track_inactives.isChecked(),
            "validate_power": self.validate_power.isChecked(),
            "power_threshold": self.power_threshold.value(),
            "validate_kills": self.validate_kills.isChecked(),
            "reconstruct_kills": self.reconstruct_kills.isChecked(),
            "check_cityhall": self.check_cityhall.isChecked(),
            "min_ch_level": self.min_ch_level.value()
        })
        
        for timing_key, spin in self.timing_spins.items():
            self.config["scan"]["timings"][timing_key] = spin.value() / 10.0
        
        if "performance" not in self.config:
            self.config["performance"] = {}
            
        self.config["performance"].update({
            "parallel_enabled": self.enable_parallel.isChecked(),
            "worker_threads": self.thread_count.value(),
            "cache_enabled": self.enable_cache.isChecked(),
            "cache_lifetime": self.cache_lifetime.value(),
            "image_optimization": self.enable_optimization.isChecked(),
            "image_quality": self.image_quality.value()
        })
        
        try:
            with open(str(get_app_root() / "config.json"), "w") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {str(e)}")
            return
        
        super().accept()

if __name__ == "__main__":
    try:
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
    except:
        pass
        
    app = QApplication(sys.argv)
    
    def init_adb_server(max_retries=3, retry_delay=2):
        adb_path = str(Path(get_app_root()) / "deps" / "platform-tools" / "adb.exe")
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Initializing ADB server (attempt {attempt + 1}/{max_retries})...")
                
                os.system(f'"{adb_path}" kill-server')
                time.sleep(1)
                
                os.environ['ANDROID_ADB_SERVER_PORT'] = str(5037)
                result = os.system(f'"{adb_path}" start-server')
                
                if result != 0:
                    raise RuntimeError(f"ADB server start failed with code {result}")
                
                devices_result = os.system(f'"{adb_path}" devices')
                if devices_result != 0:
                    raise RuntimeError("ADB server not responding to device query")
                    
                logger.info("ADB server initialized successfully")
                return True
                
            except Exception as e:
                logger.warning(f"ADB initialization attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(f"Failed to initialize ADB server after {max_retries} attempts: {e}")
    
    try:
        init_adb_server()
    except Exception as e:
        logger.error(f"Failed to initialize ADB server: {e}")
        QMessageBox.critical(None, "Error", 
            f"Failed to initialize ADB server: {e}\n\n"
            "Please ensure:\n"
            "1. The application is running as administrator\n"
            "2. No other ADB instances are running\n"
            "3. Your antivirus is not blocking ADB"
        )
        sys.exit(1)

    try:
        window = UnifiedScannerApp()
        window.show()
        
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"Critical error starting application: {e}", exc_info=True)
        QMessageBox.critical(None, "Critical Error", f"Failed to start application: {e}")
        sys.exit(1)