"""Windows shell identity and optional compositor material for native Qt frames."""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget


def set_taskbar_identity() -> None:
    if sys.platform == "win32":
        # Keep grouping and jump lists associated with pre-rename installations.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Calender.Desktop")


def transparency_allowed() -> bool:
    if sys.platform == "win32":
        import winreg

        class HighContrast(ctypes.Structure):
            _fields_ = [("size", wintypes.UINT), ("flags", wintypes.DWORD), ("scheme", wintypes.LPWSTR)]

        contrast = HighContrast()
        contrast.size = ctypes.sizeof(contrast)
        if ctypes.windll.user32.SystemParametersInfoW(0x42, contrast.size, ctypes.byref(contrast), 0) and contrast.flags & 1:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
        except OSError:
            pass
    return True


def enable_system_frost(window: QWidget) -> bool:
    """Request Desktop Acrylic before the window is shown. Keep its OS caption."""
    if (sys.platform != "win32" or QApplication.platformName() != "windows"
            or sys.getwindowsversion().build < 22621 or not transparency_allowed()):
        return False

    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    hwnd = wintypes.HWND(int(window.winId()))
    dwm = ctypes.windll.dwmapi

    def attribute(key: int, value: int) -> int:
        data = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(hwnd, key, ctypes.byref(data), ctypes.sizeof(data))

    attribute(20, 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE: match the light Qt palette.
    if attribute(38, 3) != 0:  # DWMWA_SYSTEMBACKDROP_TYPE / DWMSBT_TRANSIENTWINDOW
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        return False
    # A negative MARGINS extends composition over the entire client area. Qt
    # still owns a normal framed HWND and paints text and controls above it.
    margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
    if dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) != 0:
        attribute(38, 1)  # DWMSBT_NONE
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        return False
    return True
