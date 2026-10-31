#!/usr/bin/env python3
"""Converts an early test capture into an app session, for replay and building."""
import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ubuntu_ferret_scanner.calibration import LONG_MODE
from ubuntu_ferret_scanner.session import Session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", help="capture path without .raw")
    parser.add_argument("colour", help="colour video")
    parser.add_argument("out", help="new session folder")
    parser.add_argument("--shift-ms", type=float, default=178.0)
    args = parser.parse_args()

    session = Session(args.out)
    session.write_meta(LONG_MODE)
    shutil.copy(args.prefix + ".raw", session.depth_path)
    with open(args.prefix + ".csv") as f, open(session.depth_index, "w", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(("frame", "host_ms", "bytes", "pts"))
        for row in csv.DictReader(f):
            writer.writerow((row["frame"], row["host_ms"], row["bytes"], row["pts"]))

    probe = subprocess.run(["ffprobe", "-hide_banner", "-loglevel", "error", "-select_streams", "v",
                            "-show_entries", "packet=pts_time", "-of", "csv=p=0", args.colour],
                           check=True, capture_output=True, text=True)
    times = [float(x.strip().rstrip(",")) * 1000 + args.shift_ms for x in probe.stdout.split() if x.strip()]
    with tempfile.TemporaryDirectory(dir=session.folder) as scratch:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", args.colour, "-c", "copy",
                        "-fps_mode", "passthrough", f"{scratch}/%05d.jpg"], check=True)
        with open(session.colour_path, "wb") as data, open(session.colour_index, "w", newline="") as index:
            writer = csv.writer(index)
            writer.writerow(("frame", "host_ms", "bytes", "pts"))
            for number, ms in enumerate(times):
                jpeg = Path(scratch) / f"{number + 1:05d}.jpg"
                if not jpeg.exists():
                    continue
                content = jpeg.read_bytes()
                data.write(content)
                writer.writerow((number, f"{ms:.3f}", len(content), 0))
    print(f"imported {session.folder}")


if __name__ == "__main__":
    main()
