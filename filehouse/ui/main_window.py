"""Main application window uniting navigation, views, preview, and menus."""

from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QSplitter,
    QStatusBar,
    QMenuBar,
    QMessageBox,
    QInputDialog,
)
from PySide6.QtGui import QAction, QKeySequence, QCloseEvent

from filehouse.core.config import AppConfig
from filehouse.core.history import NavigationHistory
from filehouse.core.file_ops import open_file_with_default_app
from filehouse.ui.widgets.address_bar import AddressBar
from filehouse.ui.views.tree_view import DirectoryTreeView
from filehouse.ui.views.file_view import FileTableView
from filehouse.ui.views.preview_panel import PreviewPanel


class MainWindow(QMainWindow):
    """Main window for the FileHouse file browser."""

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.history = NavigationHistory(self.config.last_directory)

        self.setWindowTitle("FileHouse - File Browser")
        self.resize(1100, 700)

        self._build_ui()
        self._build_menus()
        self._connect_signals()

        # Restore saved window state if available
        geom, state = self.config.restore_window_state()
        if geom:
            self.restoreGeometry(geom)
        if state:
            self.restoreState(state)

        # Initial navigation
        self.navigate_to(self.history.current, record_history=False)

    def _build_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Navigation Address Bar
        self.address_bar = AddressBar(self)
        main_layout.addWidget(self.address_bar)

        # Splitter with 3 panels: Tree Sidebar | File Table | Preview Panel
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self.tree_view = DirectoryTreeView(self)
        self.file_view = FileTableView(self)
        self.preview_panel = PreviewPanel(self)

        self.splitter.addWidget(self.tree_view)
        self.splitter.addWidget(self.file_view)
        self.splitter.addWidget(self.preview_panel)

        # Set initial splitter proportions (Sidebar: 220, Files: 600, Preview: 280)
        self.splitter.setSizes([220, 600, 280])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)

        main_layout.addWidget(self.splitter)

        # Status Bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

    def _build_menus(self):
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        new_folder_action = QAction("&New Folder...", self)
        new_folder_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        new_folder_action.triggered.connect(self._create_new_folder)
        file_menu.addAction(new_folder_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View Menu
        view_menu = menubar.addMenu("&View")

        self.act_show_hidden = QAction("Show &Hidden Files", self, checkable=True)
        self.act_show_hidden.setShortcut(QKeySequence("Ctrl+H"))
        self.act_show_hidden.setChecked(self.config.show_hidden_files)
        self.act_show_hidden.toggled.connect(self._toggle_hidden_files)
        view_menu.addAction(self.act_show_hidden)

        self.act_show_preview = QAction("Show &Preview Panel", self, checkable=True)
        self.act_show_preview.setShortcut(QKeySequence("Ctrl+P"))
        self.act_show_preview.setChecked(self.config.show_preview_panel)
        self.act_show_preview.toggled.connect(self._toggle_preview_panel)
        view_menu.addAction(self.act_show_preview)

        view_menu.addSeparator()

        refresh_action = QAction("&Refresh", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self._refresh_current_view)
        view_menu.addAction(refresh_action)

        # Help Menu
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About FileHouse", self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _connect_signals(self):
        # Address bar navigation
        self.address_bar.navigate_requested.connect(self.navigate_to)
        self.address_bar.back_requested.connect(self._go_back)
        self.address_bar.forward_requested.connect(self._go_forward)
        self.address_bar.up_requested.connect(self._go_up)
        self.address_bar.home_requested.connect(lambda: self.navigate_to(Path.home()))
        self.address_bar.refresh_requested.connect(self._refresh_current_view)

        # Views interaction
        self.tree_view.directory_selected.connect(self.navigate_to)
        self.file_view.directory_opened.connect(self.navigate_to)
        self.file_view.file_activated.connect(self._open_file)
        self.file_view.selection_changed.connect(self._on_selection_changed)

        # Apply initial settings
        self._toggle_hidden_files(self.config.show_hidden_files)
        self._toggle_preview_panel(self.config.show_preview_panel)

    def navigate_to(self, target_path: Path | str, record_history: bool = True) -> None:
        """Navigate to a target directory and update all UI views."""
        target = Path(target_path).expanduser().resolve()
        if not target.is_dir():
            QMessageBox.warning(self, "Invalid Path", f"The directory does not exist:\n{target}")
            return

        if record_history:
            self.history.navigate_to(target)

        self.config.last_directory = target
        self.address_bar.set_path(target)
        self.address_bar.set_nav_states(self.history.can_go_back, self.history.can_go_forward)

        self.file_view.navigate_to(target)
        self.tree_view.select_path(target)
        self.preview_panel.update_preview(None)
        self._update_status_bar()

    def _go_back(self):
        target = self.history.back()
        if target:
            self.navigate_to(target, record_history=False)

    def _go_forward(self):
        target = self.history.forward()
        if target:
            self.navigate_to(target, record_history=False)

    def _go_up(self):
        parent = self.history.current.parent
        if parent != self.history.current:
            self.navigate_to(parent)

    def _refresh_current_view(self):
        curr = self.history.current
        self.file_view.navigate_to(curr)

    def _open_file(self, path: Path):
        success = open_file_with_default_app(path)
        if not success:
            QMessageBox.warning(self, "Open Error", f"Could not launch file:\n{path}")

    def _on_selection_changed(self, selected_paths: list[Path]):
        if selected_paths:
            self.preview_panel.update_preview(selected_paths[0])
        else:
            self.preview_panel.update_preview(None)
        self._update_status_bar(len(selected_paths))

    def _toggle_hidden_files(self, checked: bool):
        self.config.show_hidden_files = checked
        self.tree_view.set_show_hidden(checked)
        self.file_view.set_show_hidden(checked)

    def _toggle_preview_panel(self, checked: bool):
        self.config.show_preview_panel = checked
        self.preview_panel.setVisible(checked)

    def _create_new_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Enter new folder name:")
        if ok and name.strip():
            new_dir = self.history.current / name.strip()
            try:
                new_dir.mkdir(exist_ok=False)
                self._refresh_current_view()
            except OSError as err:
                QMessageBox.critical(self, "Error", f"Failed to create folder:\n{err}")

    def _update_status_bar(self, selected_count: int = 0):
        current = self.history.current
        try:
            items_count = sum(1 for _ in current.iterdir())
        except OSError:
            items_count = 0

        status = f" {items_count} items"
        if selected_count > 0:
            status += f" | {selected_count} item(s) selected"
        self.status_bar.showMessage(status)

    def _show_about_dialog(self):
        QMessageBox.about(
            self,
            "About FileHouse",
            "<h3>FileHouse</h3>"
            "<p>A modern file browser written in Python & PySide6 (Qt).</p>",
        )

    def closeEvent(self, event: QCloseEvent):
        # Save geometry and state upon closing
        self.config.save_window_state(self.saveGeometry(), self.saveState())
        super().closeEvent(event)
