"""Command line entry point: ``specview fetch|import|serve|list``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .api import default_home


def _lib(args):
    from .store import Library
    return Library(Path(args.home) / "library.sqlite")


def cmd_import(args):
    from .parse_docx import parse_docx
    from .versions import Version, parse_archive_name

    lib = _lib(args)
    touched = set()
    for f in args.files:
        p = Path(f)
        parsed_name = parse_archive_name(p.name)
        spec = args.spec or (parsed_name[0] if parsed_name else None)
        version = Version.parse(args.version) if args.version else (parsed_name[1] if parsed_name else None)
        if not spec or not version:
            sys.exit(f"{p.name}: cannot infer spec/version from the file name; pass --spec and --version")
        print(f"importing {spec} v{version} from {p.name}")
        lib.add_version(spec, version, parse_docx(p), source=str(p), recompute=False)
        touched.add(spec)
    for spec in touched:
        print(f"computing history for {spec}")
        lib.recompute_history(spec)


def cmd_fetch(args):
    from . import download
    from .parse_docx import parse_docx

    lib = _lib(args)
    releases = set(args.release) if args.release else None
    for spec in args.specs:
        remote = download.select(download.list_remote(spec), releases, args.latest_per_release,
                                 args.include_drafts)
        print(f"{spec}: {len(remote)} versions")
        for rv in remote:
            print(f"  v{rv.version}: download + convert")
            path = download.fetch(rv, Path(args.home) / "cache")
            lib.add_version(spec, rv.version, parse_docx(path), source=rv.url, recompute=False)
        print(f"{spec}: computing history")
        lib.recompute_history(spec)


def cmd_list(args):
    for s in _lib(args).specs():
        print(f"{s['number']:<10} {s['versions']:>3} versions  latest {s['latest']}  {s['title'] or ''}")


def cmd_serve(args):
    import uvicorn
    from .api import create_app

    static = Path(args.static) if args.static else None
    uvicorn.run(create_app(Path(args.home), static), host="127.0.0.1", port=args.port, log_level="warning")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="specview")
    ap.add_argument("--home", default=str(default_home()), help="library directory (default ~/.specview)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="import local .docx files (e.g. 38211-g10.docx)")
    p.add_argument("files", nargs="+")
    p.add_argument("--spec")
    p.add_argument("--version")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("fetch", help="download every version of the given specs from 3gpp.org")
    p.add_argument("specs", nargs="+", help="e.g. 38.211 38.212")
    p.add_argument("--release", type=int, action="append", help="limit to release(s), repeatable")
    p.add_argument("--latest-per-release", action="store_true")
    p.add_argument("--include-drafts", action="store_true", help="also fetch pre-approval 0.x-2.x drafts")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("list", help="list specs in the library")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("serve", help="run the local API for the viewer")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--static", help="directory with the built UI to serve")
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
