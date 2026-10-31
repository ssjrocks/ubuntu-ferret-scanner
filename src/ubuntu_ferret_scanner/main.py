"""Starts the app, or runs one of its command-line tasks."""

import argparse
import logging
import os
import threading
from pathlib import Path

from ubuntu_ferret_scanner import APP_ID, APP_NAME, __version__, appreports, updater


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
    path = Path(base) / "ubuntu-ferret-scanner"
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_update(install: bool) -> int:
    update = updater.check()
    updater.mark_checked()
    if update is None:
        print(f"{APP_NAME} {__version__}: no update found")
        return 0
    print(f"Update available: {update.version}")
    if install:
        updater.install(update)
        print(f"Installed {update.version}")
    return 0


def report_problem(note: str) -> int:
    done = threading.Event()
    result = {}

    def finished(ok: bool) -> None:
        result["ok"] = ok
        done.set()

    appreports.send_report(note, on_done=finished)
    done.wait(30)
    if not result.get("ok"):
        print("The report couldn't be sent. It will be retried on the next run.")
        return 1
    print("Report sent.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ubuntu-ferret-scanner")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    update = commands.add_parser("update", help="check GitHub for a newer release")
    update.add_argument("--install", action="store_true", help="install a newer release")
    report = commands.add_parser("report", help="report a problem to the developer")
    report.add_argument("note")
    build = commands.add_parser("build", help="build the models for a saved scan")
    build.add_argument("session")
    args = parser.parse_args()

    if args.command == "build":
        from ubuntu_ferret_scanner import reconstruct

        return reconstruct.main([args.session])

    folder = state_dir()
    log_file = folder / "app.log"
    logging.basicConfig(filename=log_file, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    appreports.init(APP_ID, APP_NAME, __version__, str(folder), log_file=str(log_file))

    if args.command == "update":
        return check_update(args.install)
    if args.command == "report":
        return report_problem(args.note)

    from ubuntu_ferret_scanner.gui.app import run

    return run()
