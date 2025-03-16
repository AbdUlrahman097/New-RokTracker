from typing import Tuple
from roktracker.utils.console import console
from PIL.Image import Image, new as NewImage
from com.dtmilano.android.adb.adbclient import AdbClient
from pathlib import Path
import subprocess
import socket
import configparser
import sys
import os
import time
import logging

from roktracker.utils.exceptions import AdbError
from roktracker.utils.general import to_int_or


def get_bluestacks_port(bluestacks_device_name: str, config) -> int:
    default_port = to_int_or(config["general"]["adb_port"], 5555)
    # try to read port from bluestacks config
    if config["general"]["emulator"] == "bluestacks":
        try:
            dummy = "AmazingDummy"
            with open(config["general"]["bluestacks"]["config"], "r") as config_file:
                file_content = "[" + dummy + "]\n" + config_file.read()
            bluestacks_config = configparser.RawConfigParser()
            bluestacks_config.read_string(file_content)

            for key, value in bluestacks_config.items(dummy):
                if value == f'"{bluestacks_device_name}"':
                    key_port = key.replace("display_name", "status.adb_port")
                    port = bluestacks_config.get(dummy, key_port)
                    return int(port.strip('"'))
        except:
            console.print(
                "[red]Could not parse or find bluestacks config. Defaulting to 5555.[/red]"
            )
    return default_port


class AdvancedAdbClient:
    def __init__(self, adb_path: str, port: int, bs_emu: str, inputs_path: Path):
        self.adb_path = adb_path
        self.port = port
        self.bs_emu = bs_emu
        self.inputs_path = inputs_path
        self.device_id = f"localhost:{port}"
        
        # Initialize required attributes
        self.device = None  # Will be set during start_adb
        self.player = bs_emu.lower()  # 'bluestacks' or 'ld'
        self.script_base = inputs_path
        
        # Set ADB server port in environment
        os.environ['ANDROID_ADB_SERVER_PORT'] = str(5037)  # Use standard ADB port
        
    def get_free_port(self) -> int:
        s = socket.socket()
        s.bind(("", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def set_adb_path(self, path: str) -> None:
        self.adb_path = path

    def kill_adb(self) -> None:
        """Kill ADB server"""
        try:
            subprocess.run(
                [self.adb_path, "kill-server"],
                capture_output=True,
                text=True,
                check=True
            )
        except Exception as e:
            logging.warning(f"Error killing ADB server: {str(e)}")
            # Don't raise error as this is cleanup
            pass

    def start_adb(self) -> None:
        """Start ADB server and connect to device"""
        try:
            # Kill any existing ADB server first
            self.kill_adb()
            time.sleep(1)  # Wait for server to fully stop
            
            # Start ADB server
            result = subprocess.run(
                [self.adb_path, "start-server"],
                capture_output=True,
                text=True,
                check=True
            )
            time.sleep(1)  # Wait for server to start
            
            # Connect to device
            result = subprocess.run(
                [self.adb_path, "connect", self.device_id],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Initialize ADB client
            self.device = AdbClient(serialno=self.device_id)
            
            # Verify connection
            if "connected" not in result.stdout.lower():
                raise AdbError(f"Failed to connect to device: {result.stdout}")
                
        except subprocess.CalledProcessError as e:
            raise AdbError(f"ADB command failed: {e.stdout} {e.stderr}")
        except Exception as e:
            raise AdbError(f"Failed to start ADB: {str(e)}")

    def secure_adb_shell(self, command_to_execute: str) -> str:
        result = ""
        for i in range(3):
            try:
                if not self.device:
                    self.start_adb()  # Ensure device is initialized
                if not self.device:
                    raise AdbError("Failed to initialize ADB device")
                    
                result = str(self.device.shell(command_to_execute))
                return result
            except Exception as e:
                console.print(f"[red]ADB command failed: {str(e)}[/red]")
                self.kill_adb()
                self.start_adb()
        return result

    def secure_adb_tap(self, position: Tuple[int, int]):
        self.secure_adb_shell(f"input tap {position[0]} {position[1]}")

    def secure_adb_screencap(self) -> Image:
        result = NewImage(mode="RGB", size=(1, 1))
        for i in range(3):
            try:
                if not self.device:
                    self.start_adb()  # Ensure device is initialized
                if not self.device:
                    raise AdbError("Failed to initialize ADB device")
                    
                result = self.device.takeSnapshot(reconnect=True)
                return result
            except Exception as e:
                console.print(f"[red]Screenshot failed: {str(e)}[/red]")
                self.kill_adb()
                self.start_adb()
        return result

    def adb_send_events(self, input_device_name: str, event_file: str | Path) -> None:
        if input_device_name == "Touch":
            if self.player == "ld":
                input_device_name = "ABS_MT_POSITION_Y"

        idn = self.secure_adb_shell(
            f"getevent -pl 2>&1 | sed -n '/^add/{{h}}/{input_device_name}/{{x;s/[^/]*//p}}'",
        )
        idn = str(idn).strip()
        macroFile = open(self.script_base / self.player / event_file, "r")
        lines = macroFile.readlines()

        for line in lines:
            self.secure_adb_shell(f"""sendevent {idn} {line.strip()}""")
