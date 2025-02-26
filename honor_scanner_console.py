import logging
import threading
from dummy_root import get_app_root
from roktracker.utils.check_python import check_py_version
from roktracker.utils.exception_handling import ConsoleExceptionHander
from roktracker.utils.output_formats import OutputFormats

logging.basicConfig(
    filename=str(get_app_root() / "honor-scanner.log"),
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


from roktracker.alliance.batch_printer import print_batch
from roktracker.honor.scanner import HonorScanner
from roktracker.utils.adb import *
from roktracker.utils.console import console
from roktracker.utils.general import *
from roktracker.utils.ocr import get_supported_langs
from roktracker.utils.validator import sanitize_scanname, validate_installation


logger = logging.getLogger(__name__)
ex_handler = ConsoleExceptionHander(logger)

sys.excepthook = ex_handler.handle_exception
threading.excepthook = ex_handler.handle_thread_exception

logger.info("Starting honor scanner console application")

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception


def ask_abort(honor_scanner: HonorScanner) -> None:
    logger.info("Prompting user to confirm scanner abortion")
    stop = questionary.confirm(
        message="Do you want to stop the scanner?:", auto_enter=False, default=False
    ).ask()

    if stop:
        logger.info("User chose to stop the scanner")
        console.print("Scan will aborted after next governor.")
        honor_scanner.end_scan()


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

    return scan_config

def safe_scan_execution(honor_scanner: HonorScanner, **scan_params) -> None:
    """Execute scan with proper error handling."""
    try:
        honor_scanner.start_scan(**scan_params)
    except AdbError as error:
        logger.error(f"ADB Connection Error: {error}")
        console.print(f"[red]ADB Connection Error[/red]: {error}")
        sys.exit(4)
    except Exception as e:
        logger.error(f"Scan Error: {e}")
        console.print(f"[red]Scan Error[/red]: {e}")
        sys.exit(5)

def main():
    logger.info("Validating installation")
    if not validate_installation().success:
        logger.error("Installation validation failed")
        sys.exit(2)

    root_dir = get_app_root()
    logger.info("Loading configuration")
    try:
        config = load_config()
    except ConfigError as e:
        logger.fatal(str(e))
        console.log(str(e))
        sys.exit(3)
    except Exception as e:
        logger.fatal(f"Unexpected error loading config: {str(e)}")
        console.log(f"Unexpected error loading config: {str(e)}")
        sys.exit(3)

    logger.info("Displaying available Tesseract languages")
    console.print(
        "Tesseract languages available: "
        + get_supported_langs(str(root_dir / "deps" / "tessdata"))
    )

    scan_config = load_scan_configuration(config)

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
        logger.info("No formats selected, exiting")
        console.print("Exiting, no formats selected.")
        return
    
    save_formats.from_list(save_formats_tmp)

    try:
        logger.info("Initializing HonorScanner")
        honor_scanner = HonorScanner(scan_config['port'], config)
        honor_scanner.set_batch_callback(print_batch)

        logger.info(f"Scan UUID: {honor_scanner.run_id}")
        console.print(
            f"The UUID of this scan is [green]{honor_scanner.run_id}[/green]",
            highlight=False,
        )
        signal.signal(signal.SIGINT, lambda _, __: ask_abort(honor_scanner))

        logger.info("Starting scan")
        safe_scan_execution(
            honor_scanner,
            kingdom=scan_config['kingdom'],
            scan_amount=scan_config['scan_amount'],
            save_formats=save_formats
        )
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
    logger.info("Honor scanner console application finished")
    input("Press enter to exit...")
