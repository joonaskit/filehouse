# FileHouse 🏠

A modular file browser application built with Python and Qt (PySide6).

## Features

- **Folder Navigation Tree**: Sidebar directory tree for quick folder hierarchy navigation.
- **File Table View**: Detailed directory contents with sortable columns (Name, Size, Type, Date Modified).
- **Interactive Navigation**:
  - Address bar with editable path and quick Enter navigation
  - Back, Forward, Up to parent directory, and Home directory buttons
  - History stack management
- **Preview & Metadata Panel**:
  - Displays file size, type, and modification timestamp
  - Thumbnail preview for images (`.png`, `.jpg`, `.svg`, etc.)
  - Text snippet preview for code and text files (`.py`, `.md`, `.txt`, `.json`, etc.)
- **Persistent Preferences**:
  - Saves window dimensions, position, and last visited directory via `QSettings`
  - Toggle hidden files (`Ctrl+H`)
  - Toggle preview panel (`Ctrl+P`)
- **System Integration**:
  - Open files using the system's default application on double-click
  - Context menu with path copying to clipboard

---

## Folder Structure

```text
filehouse/
├── main.py                     # Root entrypoint launcher
├── requirements.txt            # Python dependencies (PySide6)
├── pyproject.toml              # Packaging and build specification
├── README.md                   # Project documentation
└── filehouse/                  # Core package
    ├── __init__.py
    ├── app.py                  # Application bootstrap & lifecycle
    ├── core/                   # Business logic & non-UI helpers
    │   ├── __init__.py
    │   ├── config.py           # User settings & persistence (QSettings)
    │   ├── file_ops.py         # File operations, formatting & MIME types
    │   └── history.py          # Back / Forward navigation stack
    └── ui/                     # User interface layer
        ├── __init__.py
        ├── main_window.py      # QMainWindow, layout, menus & action wiring
        ├── styles/
        │   └── style.qss       # Application stylesheet
        ├── views/
        │   ├── __init__.py
        │   ├── file_view.py    # Main table view (QTableView + QFileSystemModel)
        │   ├── preview_panel.py# File details & preview widget
        │   └── tree_view.py    # Sidebar directory tree (QTreeView)
        └── widgets/
            ├── __init__.py
            └── address_bar.py  # Navigation bar (back/forward/up/address)
```

---

## Installation & Running

### 1. Requirements

- Python 3.10+
- PySide6

```bash
pip install -r requirements.txt
```

### 2. Run the Application

```bash
python main.py
```

Or install in editable mode:

```bash
pip install -e .
filehouse
```
