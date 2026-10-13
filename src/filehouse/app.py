"""Entry point: the arguments, and whether this process is to be the application.

A start with the app running already only hands its paths over, so nothing of Qt or of
the windows is imported here: importing them is most of the time such a start would take.
"""

import argparse
import sys

from filehouse import APP_ID, __version__
from filehouse.core import locations
from filehouse.integration.instance import Instance, activation_token


def main(argv=None):
    parser = argparse.ArgumentParser(prog=APP_ID, description="A small file browser.")
    parser.add_argument("paths", nargs="*", metavar="PATH", help="folders, files or file:// URIs")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Started by the session bus for another app's "Show in folder": no window of its own
    parser.add_argument("--service", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--quit",
        action="store_true",
        help="end the process that stays in the background, once it has nothing open",
    )
    # For other programs (the shell's launcher): find by name, print, end. See core/find.py
    parser.add_argument(
        "--search",
        metavar="TEXT",
        help="print the files and folders whose names have these words, as JSON lines, and exit",
    )
    parser.add_argument(
        "--root", metavar="FOLDER", help="with --search: where to look (default: home)"
    )
    parser.add_argument(
        "--limit", type=int, metavar="N", help="with --search: stop after N results (default: 50)"
    )
    parser.add_argument(
        "--hidden", action="store_true", help="with --search: hidden files and folders too"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.search is not None:
        # Not the application, and not the one process of the session: it only reads
        from filehouse.core import find

        return find.run(
            args.search,
            root=args.root,
            limit=find.DEFAULT_LIMIT if args.limit is None else args.limit,
            show_hidden=args.hidden,
        )

    # One process for the session. When one runs already it opens the windows, and this
    # one is done: before anything of the application is set up
    instance = Instance()
    primary = instance.claim()
    if args.quit:
        if primary is False:
            return 0 if instance.ask_to_quit() else 1
        # None runs, as far as can be told
        instance.close()
        return 0
    if primary is False:
        # As URIs: a path is relative to where this process was started, not that one
        uris = [locations.uri(locations.from_argument(path)) for path in args.paths]
        if args.service or instance.send(uris, activation_token()):
            return 0
        # It does not answer: better a second process than no window

    # Only now what an application needs: see the top of this module
    from filehouse.application import run

    return run(args, instance)


if __name__ == "__main__":
    sys.exit(main())
