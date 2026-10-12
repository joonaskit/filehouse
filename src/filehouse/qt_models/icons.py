"""Icons for content types, cached per kind.

A file's icon is a glyph on a rounded tile, coloured by what kind of file it
is. The colours are the theme's palette; which kind gets which is decided here.
"""

from functools import cache

from filehouse.integration import style

# kind -> (icon theme names to try, palette colour)
_KINDS = {
    "folder": (("folder-symbolic", "folder"), "cyan"),
    "image": (("image-x-generic-symbolic", "image-x-generic"), "purple"),
    "video": (("video-x-generic-symbolic", "video-x-generic"), "red"),
    "audio": (("audio-x-generic-symbolic", "audio-x-generic"), "orange"),
    "archive": (("package-x-generic-symbolic", "package-x-generic"), "green"),
    "document": (("x-office-document-symbolic", "x-office-document"), "yellow"),
    "code": (("text-x-script-symbolic", "text-x-script"), "indigo"),
    "other": (("text-x-generic-symbolic", "text-x-generic"), "blue"),
}

_ARCHIVES = {
    "application/zip",
    "application/gzip",
    "application/x-tar",
    "application/x-compressed-tar",
    "application/x-bzip2-compressed-tar",
    "application/x-xz-compressed-tar",
    "application/x-zstd-compressed-tar",
    "application/x-7z-compressed",
    "application/vnd.rar",
    "application/x-rar",
    "application/x-xz",
    "application/zstd",
    "application/x-bzip2",
}
_DOCUMENTS = {"application/pdf", "application/epub+zip", "text/plain", "text/markdown", "text/rtf"}
_CODE = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-shellscript",
    "application/x-desktop",
    "application/toml",
    "application/yaml",
    "application/x-yaml",
}
_OFFICE_WORDS = ("document", "spreadsheet", "presentation", "msword", "ms-excel", "ms-powerpoint")


def kind_of(content_type):
    """Which sort of file a content type is: one of the keys of the tile table."""
    if content_type == "inode/directory":
        return "folder"
    family = content_type.partition("/")[0]
    if family in ("image", "video", "audio"):
        return family
    if content_type in _ARCHIVES:
        return "archive"
    if content_type in _DOCUMENTS or any(word in content_type for word in _OFFICE_WORDS):
        return "document"
    if family == "text" or content_type in _CODE:
        return "code"
    return "other"


def icon_for_content_type(content_type):
    return _tile(kind_of(content_type), style.generation)


@cache
def _tile(kind, _theme_generation):
    # One per kind and theme: a tile is drawn in the colours of the theme in use
    names, colour = _KINDS[kind]
    return style.tile(*names, colour=colour)
