"""Build the portable executable or the installer's unpacked Windows runtime."""
import argparse
from io import BytesIO
from pathlib import Path
import os
import struct
import sys

import PyInstaller.__main__
from PyInstaller.utils.win32 import versioninfo as vi
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from school_calendar import APP_NAME, __version__

ASSETS = ROOT / "school_calendar" / "assets"
BUILD = ROOT / "build"


def windows_icon(target: Path) -> None:
    renderer = QSvgRenderer(str(ASSETS / "calendar.svg"))
    if not renderer.isValid():
        raise RuntimeError("The calendar outline SVG could not be loaded")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    data = BytesIO()
    with target.open("wb") as output:
        output.write(struct.pack("<HHH", 0, 1, len(sizes)))
        for size in sizes:
            image = QImage(size, size, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                raise RuntimeError("Could not render the Windows icon")
            png = bytes(buffer.data())
            offset = 6 + len(sizes) * 16 + data.tell()
            output.write(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset))
            data.write(png)
        output.write(data.getvalue())


def windows_version(target: Path) -> None:
    version = (*map(int, __version__.split(".")), 0)
    strings = {
        "ProductName": APP_NAME, "FileDescription": APP_NAME, "InternalName": APP_NAME,
        "OriginalFilename": f"{APP_NAME}.exe", "ProductVersion": __version__, "FileVersion": __version__,
    }
    table = vi.StringTable("040904B0", [vi.StringStruct(key, value) for key, value in strings.items()])
    info = vi.VSVersionInfo(vi.FixedFileInfo(filevers=version, prodvers=version), [
        vi.StringFileInfo([table]), vi.VarFileInfo([vi.VarStruct("Translation", [0x409, 1200])]),
    ])
    target.write_text(str(info), encoding="utf-8")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this build on Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer-runtime", action="store_true", help="Leave the Qt libraries beside the installed app.")
    options = parser.parse_args()
    # Qt links against Windows' ICU. An unrelated tool's ICU on PATH can
    # satisfy the filename search at build time but lack the required exports.
    windows = Path(os.environ["SystemRoot"])
    base_python = Path(sys.base_prefix)
    os.environ["PATH"] = os.pathsep.join(map(str, (
        Path(sys.executable).parent, base_python, base_python / "DLLs",
        windows / "System32", windows, windows / "System32" / "Wbem",
    )))
    app = QApplication([])
    BUILD.mkdir(exist_ok=True)
    ico = BUILD / f"{APP_NAME}.ico"
    windows_icon(ico)
    version_file = BUILD / "windows-version.txt"
    windows_version(version_file)
    work = BUILD / ("installer-runtime" if options.installer_runtime else "pyinstaller")
    destination = ROOT / "dist" / "installer-runtime" if options.installer_runtime else ROOT / "dist"
    PyInstaller.__main__.run([
        "--noconfirm", "--onedir" if options.installer_runtime else "--onefile", "--windowed", "--noupx", "--name", APP_NAME,
        "--icon", str(ico), "--version-file", str(version_file), "--add-data", f"{ASSETS}:school_calendar/assets",
        "--add-data", f"{ROOT / 'LICENSE'}:.",
        "--specpath", str(BUILD), "--workpath", str(work), "--distpath", str(destination),
        str(ROOT / "launch.pyw"),
    ])
