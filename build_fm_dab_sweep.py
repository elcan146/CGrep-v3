#!/usr/bin/env python3
"""
Build a standalone Windows executable for fm_dab_sweep.py.

Produces a one-directory PyInstaller bundle in dist/fm_dab_sweep/.
The output can be zipped and moved to any Windows PC; it contains its own
Python runtime and the required RTL-SDR DLLs.

Usage:
    python build_fm_dab_sweep.py
"""

import os
import shutil
import subprocess
import sys

SCRIPT = "fm_dab_sweep.py"
APP_NAME = "fm_dab_sweep"
DLLS = ["librtlsdr.dll", "libusb-1.0.dll"]


def ensure_dlls():
    """Warn if the native RTL-SDR DLLs are not next to the script."""
    missing = [dll for dll in DLLS if not os.path.exists(dll)]
    if missing:
        print("\n[!] WARNING: The following native DLLs are missing from the project folder:")
        for dll in missing:
            print(f"    - {dll}")
        print("\n    The build will still run, but the resulting executable will fail on PCs")
        print("    that do not already have these DLLs in their PATH or in the same folder.")
        print("\n    Place the DLLs next to this script before building, or copy them into")
        print("    dist/fm_dab_sweep/ after the build is complete.\n")
    else:
        print("[+] All native RTL-SDR DLLs found.\n")


def ensure_pyinstaller():
    """Install PyInstaller if it is not already available."""
    try:
        import PyInstaller  # noqa: F401
        print("[+] PyInstaller is already installed.\n")
    except ImportError:
        print("[*] PyInstaller not found. Installing...\n")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def build():
    """Run PyInstaller with the right options and data files."""
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        APP_NAME,
        "--onedir",
        "--console",
        "--clean",
        "--noconfirm",
        "--hidden-import",
        "sqlalchemy.ext.baked",
        SCRIPT,
    ]

    for dll in DLLS:
        if os.path.exists(dll):
            cmd.extend(["--add-data", f"{dll};."])

    print("[*] Running PyInstaller...")
    print("    ", " ".join(cmd))
    print()
    subprocess.check_call(cmd)


def copy_output_notes():
    """Print where the final bundle lives."""
    dist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", APP_NAME)
    print(f"\n[+] Build complete.\n")
    print(f"    Output folder: {dist_dir}")
    print(f"    Run the executable: {os.path.join(dist_dir, APP_NAME + '.exe')}")
    print(f"\n    Zip the entire '{APP_NAME}' folder and copy it to the target Windows PC.\n")


if __name__ == "__main__":
    if os.name != "nt":
        print("[!] This build script is intended for Windows.\n")
        sys.exit(1)

    ensure_dlls()
    ensure_pyinstaller()
    build()
    copy_output_notes()
