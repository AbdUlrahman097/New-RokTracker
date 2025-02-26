import logging
import threading
from dummy_root import get_app_root
from roktracker.utils.check_python import check_py_version
from roktracker.utils.exception_handling import ConsoleExceptionHander
from roktracker.utils.output_formats import OutputFormats

logging.basicConfig(
    filename=str(get_app_root() / "kingdom-scanner.log"),
    encoding="utf-8",
    format="%(asctime)s %(module)s %(levelname)s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

check_py_version((3, 11))

import json
import questionary
import signal
import sys

from roktracker.kingdom.governor_printer import print_gov_state
from roktracker.kingdom.scanner import KingdomScanner
from roktracker.utils.adb import *
from roktracker.utils.console import console
from roktracker.utils.general import *
from roktracker.utils.ocr import get_supported_langs
from roktracker.utils.validator import sanitize_scanname, validate_installation


logger = logging.getLogger(__name__)
ex_handler = ConsoleExceptionHander(logger)

logger.info("Application started")

sys.excepthook = ex_handler.handle_exception
threading.excepthook = ex_handler.handle_thread_exception


SCAN_MODES = {
    "full": "Full (Everything the scanner can)",
    "seed": "Seed (ID, Name, Power, KP, Alliance)",
    "custom": "Custom (select needed items in next step)"
}

DEFAULT_SCAN_OPTIONS = {
    "ID": False,
    "Name": False,
    "Power": False,
    "Killpoints": False,
    "Alliance": False,
    "T1 Kills": False,
    "T2 Kills": False,
    "T3 Kills": False,
    "T4 Kills": False,
    "T5 Kills": False,
    "Ranged": False,
    "Deads": False,
    "Rss Assistance": False,
    "Rss Gathered": False,
    "Helps": False,
}

def load_scan_configuration(config: dict) -> dict:
    """Load and validate scan configuration from user input."""
    scan_config = {}
    
    scan_config['bluestacks_name'] = questionary.text(
        message="Name of your bluestacks instance:",
        default=config["general"]["bluestacks"]["name"]
    ).unsafe_ask()
    
    scan_config['port'] = int(questionary.text(
        f"Adb port of device (detected {get_bluestacks_port(scan_config['bluestacks_name'], config)}):",
        default=str(get_bluestacks_port(scan_config['bluestacks_name'], config)),
        validate=lambda port: is_string_int(port)
    ).unsafe_ask())
    
    scan_config['kingdom'] = questionary.text(
        message="Kingdom name (used for file name):",
        default=config["scan"]["kingdom_name"]
    ).unsafe_ask()

    validated_name = sanitize_scanname(scan_config['kingdom'])
    while not validated_name.valid:
        scan_config['kingdom'] = questionary.text(
            message="Kingdom name (Previous name was invalid):",
            default=validated_name.result
        ).unsafe_ask()
        validated_name = sanitize_scanname(scan_config['kingdom'])

    scan_config['scan_amount'] = int(questionary.text(
        message="Number of people to scan:",
        validate=lambda port: is_string_int(port),
        default=str(config["scan"]["people_to_scan"])
    ).unsafe_ask())

    scan_config['resume_scan'] = questionary.confirm(
        message="Resume scan:",
        auto_enter=False,
        default=config["scan"]["resume"]
    ).unsafe_ask()

    scan_config['new_scroll'] = questionary.confirm(
        message="Use advanced scrolling method:",
        auto_enter=False,
        default=config["scan"]["advanced_scroll"]
    ).unsafe_ask()
    config["scan"]["advanced_scroll"] = scan_config['new_scroll']

    scan_config['track_inactives'] = questionary.confirm(
        message="Screenshot inactives:",
        auto_enter=False,
        default=config["scan"]["track_inactives"]
    ).unsafe_ask()

    return scan_config

def get_scan_options(scan_mode: str) -> dict:
    """Get scan options based on selected mode."""
    base_options = DEFAULT_SCAN_OPTIONS.copy()
    
    scan_presets = {
        "full": dict.fromkeys(base_options.keys(), True),
        "seed": {**base_options, **{
            "ID": True, "Name": True, "Power": True,
            "Killpoints": True, "Alliance": True
        }},
    }
    
    return scan_presets.get(scan_mode, base_options)

def safe_scan_execution(kingdom_scanner: KingdomScanner, **scan_params) -> None:
    """Execute scan with proper error handling."""
    try:
        kingdom_scanner.start_scan(**scan_params)
    except AdbError as error:
        logger.error(f"ADB Connection Error: {error}")
        console.print(f"[red]ADB Connection Error[/red]: {error}")
        sys.exit(4)
    except Exception as e:
        logger.error(f"Scan Error: {e}")
        console.print(f"[red]Scan Error[/red]: {e}")
        sys.exit(5)

def validate_numeric_input(prompt: str, default: str, min_value: float = 0.0, max_value: float = float('inf')) -> float:
    """Validate numeric input within specified range."""
    def validator(value: str) -> bool:
        try:
            num = float(value)
            if min_value is not None and num < min_value:
                return False
            if max_value is not None and num > max_value:
                return False
            return True
        except ValueError:
            return False
    
    return float(questionary.text(
        message=prompt,
        default=default,
        validate=validator
    ).unsafe_ask())

def ask_abort(kingdom_scanner: KingdomScanner) -> None:
    stop = questionary.confirm(
        message="Do you want to stop the scanner?:", auto_enter=False, default=False
    ).ask()

    if stop:
        console.print("Scan will aborted after next governor.")
        kingdom_scanner.end_scan()


def ask_continue(msg: str) -> bool:
    return questionary.confirm(message=msg, auto_enter=False, default=False).ask()

def main():
    logger.info("Starting main function")
    if not validate_installation().success:
        logger.error("Installation validation failed")
        sys.exit(2)
    root_dir = get_app_root()
    logger.info(f"Application root directory: {root_dir}")

    try:
        config = load_config()
        logger.info("Configuration loaded successfully")
    except ConfigError as e:
        logger.fatal(str(e))
        console.log(str(e))
        sys.exit(3)
    except Exception as e:
        logger.fatal(f"Unexpected error loading config: {str(e)}")
        console.log(f"Unexpected error loading config: {str(e)}")
        sys.exit(3)

    scan_config = load_scan_configuration(config)

    console.print(
        "Tesseract languages available: "
        + get_supported_langs(str(root_dir / "deps" / "tessdata"))
    )

    logger.info("Prompting user for scan mode")
    scan_mode = questionary.select(
        "What scan do you want to do?",
        choices=[
            questionary.Choice(
                "Full (Everything the scanner can)",
                value="full",
                checked=True,
                shortcut_key="f",
            ),
            questionary.Choice(
                "Seed (ID, Name, Power, KP, Alliance)",
                value="seed",
                checked=False,
                shortcut_key="s",
            ),
            questionary.Choice(
                "Custom (select needed items in next step)",
                value="custom",
                checked=False,
                shortcut_key="c",
            ),
        ],
    ).unsafe_ask()
    logger.info(f"Selected scan mode: {scan_mode}")

    scan_options = get_scan_options(scan_mode)

    if scan_mode == "custom":
        items_to_scan = questionary.checkbox(
            "What stats should be scanned?",
            choices=[
                questionary.Choice("ID", checked=False),
                questionary.Choice("Name", checked=False),
                questionary.Choice("Power", checked=False),
                questionary.Choice("Killpoints", checked=False),
                questionary.Choice("Alliance", checked=False),
                questionary.Choice("T1 Kills", checked=False),
                questionary.Choice("T2 Kills", checked=False),
                questionary.Choice("T3 Kills", checked=False),
                questionary.Choice("T4 Kills", checked=False),
                questionary.Choice("T5 Kills", checked=False),
                questionary.Choice("Ranged", checked=False),
                questionary.Choice("Deads", checked=False),
                questionary.Choice("Rss Assistance", checked=False),
                questionary.Choice("Rss Gathered", checked=False),
                questionary.Choice("Helps", checked=False),
            ],
        ).unsafe_ask()
        if not items_to_scan:
            console.print("Exiting, no items selected.")
            logger.info("No items selected for custom scan, exiting")
            return
        for item in items_to_scan:
            scan_options[item] = True

    logger.info("Prompting user for validation options")
    validate_kills = False
    reconstruct_fails = False

    if (
        scan_options["T1 Kills"]
        and scan_options["T2 Kills"]
        and scan_options["T3 Kills"]
        and scan_options["T4 Kills"]
        and scan_options["T5 Kills"]
        and scan_options["Killpoints"]
    ):
        validate_kills = questionary.confirm(
            message="Validate killpoints:",
            auto_enter=False,
            default=config["scan"]["validate_kills"],
        ).unsafe_ask()

    if validate_kills:
        reconstruct_fails = questionary.confirm(
            message="Try reconstructiong wrong kills values:",
            auto_enter=False,
            default=config["scan"]["reconstruct_kills"],
        ).unsafe_ask()

    validate_power = questionary.confirm(
        message="Validate power (only works in power ranking):",
        auto_enter=False,
        default=config["scan"]["validate_power"],
    ).unsafe_ask()

    power_threshold = validate_numeric_input(
        "Power threshold to trigger warning:",
        default=str(config["scan"]["power_threshold"]),
        min_value=0
    )

    config["scan"]["timings"]["info_close"] = validate_numeric_input(
        "Time to wait after more info close:",
        default=str(config["scan"]["timings"]["info_close"]),
        min_value=0
    )

    config["scan"]["timings"]["gov_close"] = validate_numeric_input(
        "Time to wait after governor close:",
        default=str(config["scan"]["timings"]["gov_close"]),
        min_value=0
    )

    save_formats = OutputFormats()
    save_formats_tmp = questionary.checkbox(
        "In what format should the result be saved?",
        choices=[
            questionary.Choice(
                "Excel (xlsx)",
                value="xlsx",
                checked=config["scan"]["formats"]["xlsx"],
            ),
            questionary.Choice(
                "Comma seperated values (csv)",
                value="csv",
                checked=config["scan"]["formats"]["csv"],
            ),
            questionary.Choice(
                "JSON Lines (jsonl)",
                value="jsonl",
                checked=config["scan"]["formats"]["jsonl"],
            ),
        ],
    ).unsafe_ask()

    if not save_formats_tmp:
        console.print("Exiting, no formats selected.")
        logger.info("No formats selected, exiting")
        return
    save_formats.from_list(save_formats_tmp)

    try:
        kingdom_scanner = KingdomScanner(config, scan_options, scan_config['port'])
        kingdom_scanner.set_continue_handler(ask_continue)
        kingdom_scanner.set_governor_callback(print_gov_state)

        console.print(
            f"The UUID of this scan is [green]{kingdom_scanner.run_id}[/green]",
            highlight=False,
        )
        logger.info(f"Scan UUID: {kingdom_scanner.run_id}")

        signal.signal(signal.SIGINT, lambda _, __: ask_abort(kingdom_scanner))

        safe_scan_execution(
            kingdom_scanner,
            kingdom=scan_config['kingdom'],
            scan_amount=scan_config['scan_amount'],
            resume_scan=scan_config['resume_scan'],
            track_inactives=scan_config['track_inactives'],
            validate_kills=validate_kills,
            reconstruct_fails=reconstruct_fails,
            validate_power=validate_power,
            power_threshold=power_threshold,
            save_formats=save_formats,
        )
        logger.info("Scan started")
    except AdbError as error:
        logger.error(
            "An error with the adb connection occured (probably wrong port). Exact message: "
            + str(error)
        )
        console.print(
            "An error with the adb connection occured. Please verfiy that you use the correct port.\nExact message: "
            + str(error)
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        console.print(f"Unexpected error: {str(e)}")

if __name__ == "__main__":
    main()
    logger.info("Application finished")
    input("Press enter to exit...")
