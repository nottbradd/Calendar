import sys

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QMessageBox

from .appearance import apply_theme
from .storage import Store, StoreError, database_path
from .window import CalendarWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("School Calendar")
    app.setApplicationVersion("1.0.0")
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
        QMessageBox.critical(None, "School Calendar could not open",
                             f"{exc}\n\nCalendar file:\n{path}\n\nCheck file access or restore a known good backup.")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
