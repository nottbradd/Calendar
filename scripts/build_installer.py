"""Build the per-user Windows installer and its downloadable SHA-256 checksum."""
import argparse
import hashlib
from importlib.metadata import distribution
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from school_calendar import APP_NAME, __version__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, help="Path to the Inno Setup 6 command-line compiler.")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64") or sys.maxsize <= 2**32:
        parser.error("Build with x64 Python on Windows.")
    compiler = args.iscc or shutil.which("ISCC.exe")
    if not compiler or not Path(compiler).is_file():
        parser.error("Install Inno Setup 6 and pass the path to ISCC.exe with --iscc.")
    compiler = Path(compiler).resolve()
    subprocess.run([sys.executable, str(ROOT / "scripts/build_windows.py"), "--installer-runtime"], check=True)

    bundle = ROOT / "dist/installer-runtime" / APP_NAME
    notices = bundle / "licenses"
    shutil.copytree(ROOT / "installer/licenses", notices, dirs_exist_ok=True)
    pyinstaller = distribution("pyinstaller")
    bootloader_license = next(p for p in pyinstaller.files if p.name == "COPYING.txt" and ".dist-info" in str(p))
    for src, name in (
        (ROOT / "installer/THIRD-PARTY.md", "README.md"),
        (ROOT / "school_calendar/assets/fonts/LICENSE.md", "Source-Sans-3.md"),
        (Path(sys.base_prefix) / "LICENSE.txt", "Python.txt"),
        (compiler.parent / "license.txt", "Inno-Setup.txt"),
        (pyinstaller.locate_file(bootloader_license), "PyInstaller.txt"),
    ):
        shutil.copyfile(src, notices / name)

    release = ROOT / "dist/release"
    release.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), "/Qp", f"/DAppVersion={__version__}", f"/DBundleDir={bundle}",
                    f"/DReleaseDir={release}", str(ROOT / "installer/Calendar.iss")], check=True)
    setup = release / f"{APP_NAME}-{__version__}-Setup.exe"
    with setup.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (release / "SHA256SUMS.txt").write_text(f"{digest}  {setup.name}\n", encoding="ascii")
    print(f"{setup}\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
