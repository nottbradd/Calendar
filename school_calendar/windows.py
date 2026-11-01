"""Windows shell identity and optional compositor material for native Qt frames."""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget


def set_taskbar_identity() -> None:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Calender.Desktop")


def enable_system_frost(window: QWidget) -> bool:
    """Request Desktop Acrylic before the window is shown. Keep its OS caption."""
    if sys.platform != "win32" or QApplication.platformName() != "windows" or sys.getwindowsversion().build < 22621:
        return False

    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    hwnd = wintypes.HWND(int(window.winId()))
    dwm = ctypes.windll.dwmapi

    def attribute(key: int, value: int) -> int:
        data = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(hwnd, key, ctypes.byref(data), ctypes.sizeof(data))

    attribute(20, 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE: match the light Qt palette.
    if attribute(38, 3) != 0:  # DWMWA_SYSTEMBACKDROP_TYPE / DWMSBT_TRANSIENTWINDOW
        return False
    # A negative MARGINS extends composition over the entire client area. Qt
    # still owns a normal framed HWND and paints text and controls above it.
    margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
    if dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) != 0:
        attribute(38, 1)  # DWMSBT_NONE
        return False
    return True
