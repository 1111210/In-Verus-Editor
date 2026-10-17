#!/usr/bin/env python3
"""Build the Windows onedir release using Tcl/Tk 9 zipfs libraries."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import _tkinter


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = PROJECT_ROOT / "_build"
RELEASE_ROOT = PROJECT_ROOT / "release"
DIST_ROOT = RELEASE_ROOT / "InVerusEditor"
ARCHIVE_BASE = RELEASE_ROOT / "InVerusEditor-windows-x64"


def verify_tcl_tk_runtime() -> None:
    tcl_major = int(str(_tkinter.TCL_VERSION).split(".", 1)[0])
    tk_major = int(str(_tkinter.TK_VERSION).split(".", 1)[0])
    if tcl_major != 9 or tk_major != 9:
        raise SystemExit(
            "this release configuration requires Tcl/Tk 9; "
            f"found Tcl {_tkinter.TCL_VERSION}, Tk {_tkinter.TK_VERSION}"
        )
    print(f"Tcl/Tk runtime: {_tkinter.TCL_VERSION}/{_tkinter.TK_VERSION} (zipfs)")


def build_distribution() -> Path:
    spec = PROJECT_ROOT / "packaging" / "InVerusEditor.spec"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(RELEASE_ROOT),
        "--workpath",
        str(BUILD_ROOT / "pyinstaller"),
        str(spec),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    if not (DIST_ROOT / "InVerusEditor.exe").is_file():
        raise SystemExit(f"build did not produce {DIST_ROOT / 'InVerusEditor.exe'}")
    shutil.copy2(PROJECT_ROOT / "README.md", DIST_ROOT / "README.md")
    shutil.copy2(PROJECT_ROOT / "LICENSE", DIST_ROOT / "LICENSE")
    return DIST_ROOT


def verify_distribution(dist: Path) -> None:
    internal = dist / "_internal"
    required = [
        dist / "README.md",
        dist / "LICENSE",
        internal / "tcl90.dll",
        internal / "tcl9tk90.dll",
    ]
    pygame_mixer = list((internal / "pygame").glob("mixer*.pyd"))
    numpy_core = list(
        (internal / "numpy" / "_core").glob("_multiarray_umath*.pyd")
    )
    if not pygame_mixer:
        raise SystemExit("release is missing the pygame mixer extension")
    if not numpy_core:
        raise SystemExit("release is missing the NumPy core extension")
    required.extend(pygame_mixer)
    required.extend(numpy_core)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("release is missing required files:\n" + "\n".join(missing))
    if (internal / "OpenGL").exists():
        raise SystemExit("release unexpectedly contains the unused OpenGL package")
    if (internal / "_tcl_data").exists() or (internal / "_tk_data").exists():
        raise SystemExit("release contains redundant extracted Tcl/Tk data")


def make_archive(dist: Path) -> Path:
    archive = shutil.make_archive(
        str(ARCHIVE_BASE),
        "zip",
        root_dir=dist.parent,
        base_dir=dist.name,
    )
    return Path(archive)


def main() -> int:
    verify_tcl_tk_runtime()
    dist = build_distribution()
    verify_distribution(dist)
    archive = make_archive(dist)
    print(f"Release directory: {dist}")
    print(f"Release archive:   {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
