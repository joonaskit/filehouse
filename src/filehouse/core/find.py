"""`filehouse --search`: the search by name, for another program to read.

The shell's launcher asks with it. It is the walk of `search.search_below` and nothing
else: no window, no Qt, and not the one process of the session, so it runs beside the
app and ends when it has answered. What it finds goes to standard output as it is
found, one JSON object to a line:

    {"path": "/home/me/docs/Report 2024.pdf", "name": "Report 2024.pdf",
     "folder": "/home/me/docs", "dir": false, "type": "application/pdf"}

and a last line when the walk has ended, with whether it stopped at the limit:

    {"done": true, "more": false}

A root that is no folder is one line, `{"error": "..."}`, and exit status 1. The one
who asked may end the process at any time; a closed pipe is not an error.
"""

import json
import sys

from filehouse.core import locations, search

# Enough to choose from in a launcher, and a walk of a whole home stops early
DEFAULT_LIMIT = 50


def result(root, info):
    """What is told of one result: where it is, and enough to show a row for it."""
    location = locations.entry_location(root, info)
    path = locations.local_path(location) or locations.uri(location)
    folder = locations.parent(location)
    return {
        "path": path,
        "name": info.display_name,
        "folder": (locations.local_path(folder) or locations.uri(folder)) if folder else "",
        "dir": info.is_dir,
        "type": info.content_type,
    }


def run(text, root=None, limit=DEFAULT_LIMIT, show_hidden=False, out=None):
    """Search below `root` (home when None, a path, a URI or a "~" otherwise) and write
    the lines. Returns the exit status."""
    out = sys.stdout if out is None else out

    def write(record):
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()

    folder = locations.home() if not root else locations.from_user_input(root)
    try:
        if not locations.is_folder(folder):
            write({"error": f"Not a folder: {locations.display_path(folder)}"})
            return 1
        more = search.search_below(
            folder,
            text,
            lambda batch: [write(result(folder, info)) for info in batch],
            show_hidden=show_hidden,
            limit=max(1, limit),
            include_root=True,
        )
        write({"done": True, "more": more})
    except BrokenPipeError:
        # The one who asked has gone: there is nobody to tell
        pass
    return 0
