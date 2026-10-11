"""Maps the shell's theme to Qt: palette, font, sizes and a short stylesheet."""

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from filehouse.integration import theme as theme_reader

BASE_FONT_PX = 13

# The theme in use. Complete defaults until apply() runs, so sizes work in tests.
_theme = theme_reader.merge(theme_reader.DEFAULTS, {})


def px(size):
    """An unscaled pixel size at the user's UI scale."""
    return theme_reader.scaled(_theme, size)


def color(name):
    return QColor(_theme["colors"][name])


def apply(app, theme):
    """Style the whole application. Call before creating any window."""
    global _theme
    _theme = theme
    app.setPalette(_palette())
    app.setFont(_font())
    app.setStyleSheet(_stylesheet())


def _palette():
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "background",
        QPalette.ColorRole.Base: "surface",
        QPalette.ColorRole.AlternateBase: "surfaceRaised",
        QPalette.ColorRole.Button: "surfaceRaised",
        QPalette.ColorRole.ToolTipBase: "surfaceRaised",
        QPalette.ColorRole.WindowText: "textPrimary",
        QPalette.ColorRole.Text: "textPrimary",
        QPalette.ColorRole.ButtonText: "textPrimary",
        QPalette.ColorRole.ToolTipText: "textPrimary",
        QPalette.ColorRole.PlaceholderText: "textSecondary",
        QPalette.ColorRole.Highlight: "accent",
        QPalette.ColorRole.HighlightedText: "onAccent",
        QPalette.ColorRole.Link: "accent",
    }
    for role, name in roles.items():
        palette.setColor(role, color(name))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, color("textTertiary"))
    return palette


def _font():
    installed = set(QFontDatabase.families())
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    for family in _theme["fonts"]["families"]:
        if family in installed:
            font = QFont(family)
            break
    font.setPixelSize(theme_reader.font_scaled(_theme, BASE_FONT_PX))
    return font


def _stylesheet():
    # Kept short on purpose: a large stylesheet fights the platform style
    colors = _theme["colors"]
    small = px(_theme["radius"]["small"])
    return f"""
        QToolBar {{
            border: none; spacing: {px(4)}px; padding: {px(6)}px;
            background: {colors["background"]};
        }}
        QToolButton {{
            border: none; border-radius: {small}px; padding: {px(5)}px;
            background: transparent;
        }}
        QToolButton:hover {{ background: {colors["surfaceRaised"]}; }}
        QToolButton:pressed, QToolButton:checked {{ background: {colors["surface"]}; }}
        QLineEdit {{
            border: 1px solid {colors["surfaceRaised"]}; border-radius: {small}px;
            padding: {px(4)}px {px(8)}px; background: {colors["surface"]};
        }}
        QLineEdit:focus {{ border-color: {colors["accent"]}; }}
        QTreeView, QListView {{ border: none; outline: none; }}
        QTreeView::item {{ padding: {px(5)}px {px(6)}px; }}
        QListWidget {{ background: {colors["background"]}; padding: {px(6)}px; }}
        QListWidget::item {{ padding: {px(6)}px {px(8)}px; border-radius: {small}px; }}
        QListWidget::item:hover {{ background: {colors["surface"]}; }}
        QListWidget::item:selected {{
            background: {colors["surfaceRaised"]}; color: {colors["textPrimary"]};
        }}
        QHeaderView::section {{
            border: none; padding: {px(5)}px {px(6)}px;
            background: {colors["surface"]}; color: {colors["textSecondary"]};
        }}
        QSplitter::handle {{ background: {colors["surface"]}; }}
        QStatusBar, QStatusBar QLabel {{ color: {colors["textSecondary"]}; }}
        #operations {{ border-top: 1px solid {colors["surface"]}; }}
        #operationsHeader {{ border-radius: 0; padding: {px(6)}px {px(10)}px; }}
        QProgressBar {{
            border: none; border-radius: {px(3)}px; background: {colors["surfaceRaised"]};
        }}
        QProgressBar::chunk {{ border-radius: {px(3)}px; background: {colors["accent"]}; }}
        QStatusBar QLabel {{ padding: 0 {px(8)}px; }}
    """
