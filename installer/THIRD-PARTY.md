# Calendar for Windows · Third-party software

Calendar uses Python, Qt, Qt for Python (PySide6 and Shiboken), SQLite, and the bundled Source Sans 3 font. The Windows build also includes the PyInstaller bootloader, Microsoft Visual C++ runtime files and runtime dependencies including OpenSSL and libffi. Setup is built with Inno Setup. Each component retains its upstream terms. The installed `licenses` directory contains the accompanying notices.

## Qt and Qt for Python 6.11.2

The unmodified upstream wheels supply the shared Qt libraries and Python bindings. Their licence and attribution collections include the GNU LGPL and GPL texts and notices for components in the corresponding source modules. Exact source archives are provided with the [Calendar 1.2.0 release](https://github.com/nottbradd/Calendar/releases/tag/v1.2.0).

| Upstream source | SHA-256 |
| --- | --- |
| [Qt Base](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.tar.xz) | `5b2e00eccaf5a4d8c14134ffa0ea8dfd0a35ae1ffc7f8d87fa4305a1ed23cf22` |
| [Qt SVG](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtsvg-everywhere-src-6.11.2.tar.xz) | `d594337feca84c26fb67fe87b85e6a5c12fda404b611d905f9d138210c311876` |
| [Qt Image Formats](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtimageformats-everywhere-src-6.11.2.tar.xz) | `cecd8900f34b6550076309bc94f62f828008b633a4239e0a08c86788f41001f8` |
| [PySide6 and Shiboken](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz) | `cba47efbaad1bedd529725cbc14e21f156c7a19366f07b3edfbb076ffd7afdf8` |

To use a modified library, build a compatible x64 version using the upstream source instructions. Close Calendar and replace the corresponding shared files in `_internal/PySide6` or `_internal/shiboken6` in its installation directory. You can also install compatible locally built wheels in the development environment and run the installer build described in the [project README](https://github.com/nottbradd/Calendar#verify-and-build).

## Other components

The sync prototype uses PyNaCl and libsodium for authenticated encryption and sealed key envelopes. Windows builds include their license texts, together with the cffi and pycparser notices, in the bundled Python package metadata. [PyNaCl](https://github.com/pyca/pynacl) · [libsodium](https://github.com/jedisct1/libsodium)

The installer includes the licence texts for [Python](https://www.python.org/downloads/source/), [PyInstaller](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3), [Inno Setup](https://github.com/jrsoftware/issrc/tree/is-6_7_3), [OpenSSL](https://github.com/openssl/openssl/tree/openssl-3.0.18), [libffi](https://github.com/libffi/libffi) and [Source Sans 3](https://github.com/adobe-fonts/source-sans/releases/tag/3.052R). [SQLite is in the public domain](https://sqlite.org/copyright.html).
