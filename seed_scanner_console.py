import logging
import threading
from dummy_root import get_app_root
from roktracker.utils.check_python import check_py_version
from roktracker.utils.exception_handling import ConsoleExceptionHander
from roktracker.utils.output_formats import OutputFormats

logging.basicConfig(
    filename=str(get_app_root() / "seed-scanner.log"),
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
from roktracker.seed.scanner import SeedScanner
from roktracker.utils.adb import *
from roktracker.utils.console import console
from roktracker.utils.general import *
from roktracker.utils.ocr import get_supported_langs
from roktracker.utils.validator import sanitize_scanname, validate_installation


logger = logging.getLogger(__name__)
ex_handler = ConsoleExceptionHander(logger)


sys.excepthook = ex_handler.handle_exception
threading.excepthook = ex_handler.handle_thread_exception

logger.info("Starting seed scanner console application")

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception


def ask_abort(alliance_scanner: SeedScanner) -> None:
    logger.info("Prompting user to confirm scanner abortion")
    stop = questionary.confirm(
        message="Do you want to stop the scanner?:", auto_enter=False, default=False
    ).ask()

    if stop:
        logger.info("User chose to stop the scanner")
        console.print("Scan will aborted after next governor.")
        alliance_scanner.end_scan()


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

    try:
        logger.info("Prompting user for Bluestacks instance name")
        bluestacks_device_name = questionary.text(
            message="Name of your bluestacks instance:",
            default=config["general"]["bluestacks"]["name"],
        ).unsafe_ask()

        logger.info("Prompting user for ADB port")
        bluestacks_port = int(
            questionary.text(
                f"Adb port of device (detected {get_bluestacks_port(bluestacks_device_name, config)}):",
                default=str(get_bluestacks_port(bluestacks_device_name, config)),
                validate=lambda port: is_string_int(port),
            ).unsafe_ask()
        )

        logger.info("Prompting user for alliance name")
        kingdom = questionary.text(
            message="Alliance name (used for file name):",
            default=config["scan"]["kingdom_name"],
        ).unsafe_ask()

        validated_name = sanitize_scanname(kingdom)
        while not validated_name.valid:
            kingdom = questionary.text(
                message="Alliance name (Previous name was invalid):",
                default=validated_name.result,
            ).unsafe_ask()
            validated_name = sanitize_scanname(kingdom)

        logger.info("Prompting user for number of people to scan")
        scan_amount = int(
            questionary.text(
                message="Number of people to scan:",
                validate=lambda port: is_string_int(port),
                default=str(config["scan"]["people_to_scan"]),
            ).unsafe_ask()
        )

        logger.info("Prompting user for output formats")
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

        if save_formats_tmp == [] or save_formats_tmp == None:
            logger.info("No formats selected, exiting")
            console.print("Exiting, no formats selected.")
            return
        else:
            save_formats.from_list(save_formats_tmp)
    except ValueError as e:
        logger.error(f"Value error: {str(e)}")
        console.print(f"Value error: {str(e)}")
        sys.exit(3)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        console.print(f"Unexpected error: {str(e)}")
        sys.exit(3)

    try:
        logger.info("Initializing SeedScanner")
        alliance_scanner = SeedScanner(bluestacks_port, config)
        alliance_scanner.set_batch_callback(print_batch)

        logger.info(f"Scan UUID: {alliance_scanner.run_id}")
        console.print(
            f"The UUID of this scan is [green]{alliance_scanner.run_id}[/green]",
            highlight=False,
        )
        signal.signal(signal.SIGINT, lambda _, __: ask_abort(alliance_scanner))

        logger.info("Starting scan")
        alliance_scanner.start_scan(kingdom, scan_amount, save_formats)
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
    logger.info("Seed scanner console application finished")
    input("Press enter to exit...")
