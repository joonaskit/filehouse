"""The columns of the details view: which there are, which a folder offers, and the order
and the choice that were made. No Qt: it is only names."""

# In the order of the model's columns: a key's place here is its column number
KEYS = (
    "name",
    "permissions",
    "modified",
    "size",
    "type",
    "created",
    "accessed",
    "owner",
    "group",
    "original",
    "deleted",
)
TITLES = {
    "name": "Name",
    "permissions": "Permissions",
    "modified": "Modified",
    "size": "Size",
    "type": "Type",
    "created": "Created",
    "accessed": "Accessed",
    "owner": "Owner",
    "group": "Group",
    "original": "Original location",
    "deleted": "Deleted",
}
# Unscaled widths of the columns that do not stretch
WIDTHS = {
    "permissions": 100,
    "modified": 120,
    "size": 90,
    "type": 150,
    "created": 120,
    "accessed": 120,
    "owner": 90,
    "group": 90,
    "original": 220,
    "deleted": 120,
}

# Two sets of columns, each remembered on its own: the trash has two of its own
FILES, TRASH = "files", "trash"
_ONLY_TRASH = ("original", "deleted")
_OFFERED = {
    FILES: tuple(key for key in KEYS if key not in _ONLY_TRASH),
    TRASH: KEYS,
}
_DEFAULTS = {
    FILES: ("name", "permissions", "modified", "size"),
    TRASH: ("name", "original", "deleted", "size"),
}
# What a narrow window drops: a date is the widest thing a row has
_DATES = ("modified", "created", "accessed", "deleted")
# What it keeps of the rest, besides the name
_NARROW_KEEPS = 2


def column(key):
    return KEYS.index(key)


def offered(kind):
    """The columns a folder of this kind can show, in the order the menu lists them."""
    return _OFFERED[kind]


def choose(stored, kind):
    """The columns to show, in order: what was `stored` (a list, or None for never chosen)
    with what the folder does not offer and what is repeated left out, and the name first.
    A file that was edited by hand is not trusted."""
    if not isinstance(stored, list):
        return list(_DEFAULTS[kind])
    keys = ["name"]
    for key in stored:
        if isinstance(key, str) and key in _OFFERED[kind] and key not in keys:
            keys.append(key)
    return keys


def narrow(keys):
    """What of `keys` a narrow window shows: the name, no dates, and two others."""
    return ["name", *[key for key in keys if key != "name" and key not in _DATES][:_NARROW_KEEPS]]
