import sys

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__
from .appearance import apply_theme
from .storage import Store, StoreError, database_path
from .window import CalendarWindow
from .windows import set_taskbar_identity


def main() -> int:
    set_taskbar_identity()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    QLocale.setDefault(QLocale("en_AU"))
    apply_theme(app)
    path = database_path()
    store = None
    try:
        store = Store(path)
        window = CalendarWindow(store)
        window.show()
        return app.exec()
    except StoreError as exc:
        QMessageBox.critical(None, f"{APP_NAME} could not open",
                             f"{exc}\n\nCalendar file:\n{path}\n\nCheck file access or restore a known good backup.")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
