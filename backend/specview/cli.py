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
            try:
                path = download.fetch(rv, Path(args.home) / "cache")
            except download.LegacyDocError as e:
                print(f"    skipped: {e}")
                continue
            lib.add_version(spec, rv.version, parse_docx(path), source=rv.url, recompute=False)
        print(f"{spec}: computing history")
        lib.recompute_history(spec)
        if args.with_crs:
            print(f"{spec}: fetching CR cover pages")
            print(f"{spec}: {lib.fetch_cr_details(spec, cache_dir=Path(args.home) / 'cache')}")


def cmd_fetch_crs(args):
    lib = _lib(args)
    for spec in args.specs:
        def progress(i, n, c):
            print(f"  [{i}/{n}] v{c['new_version']} CR{c['cr']} {c['tdoc']}")
        st = lib.fetch_cr_details(spec, set(args.version) if args.version else None,
                                  Path(args.home) / "cache", progress, args.retry)
        print(f"{spec}: {st}")


def cmd_fetch_features(args):
    from . import download
    from .parse_docx import parse_docx

    lib = _lib(args)
    rv = download.select(download.list_remote("38.822"))[-1]
    print(f"38.822 v{rv.version}: download + convert")
    n = lib.import_features(parse_docx(download.fetch(rv, Path(args.home) / "cache")), str(rv.version))
    print(f"{n} UE features imported")


def cmd_list(args):
    for s in _lib(args).specs():
        print(f"{s['number']:<10} {s['versions']:>3} versions  latest {s['latest']}  {s['title'] or ''}")


def cmd_serve(args):
    import uvicorn
    from .api import create_app

    static = Path(args.static) if args.static else None
    _log_to_file(Path(args.home))
    uvicorn.run(create_app(Path(args.home), static), host="127.0.0.1", port=args.port, log_level="warning",
                log_config=None)  # keep the handlers set up here


def _log_to_file(home: Path) -> None:
    """Errors go to <home>/logs/specview.log: the installed app has no console to print them on."""
    import logging
    import sys
    from logging.handlers import RotatingFileHandler

    from . import __version__

    d = home / "logs"
    d.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(d / "specview.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    if sys.stderr:  # a console when run by hand (None in the windowed build)
        root.addHandler(logging.StreamHandler())
    for noisy in ("httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("specview").info("specview %s starting, home %s", __version__, home)



def _system_certificates() -> None:
    """Trust what Windows (or macOS/Linux) trusts. Company networks that inspect HTTPS re-sign it with their own
    root certificate, which IT installs in the system store; requests' own list (certifi) does not have it, so
    every download and LLM request failed with an SSL error there. SPECVIEW_SYSTEM_CERTS=0 turns this off."""
    import os

    if os.environ.get("SPECVIEW_SYSTEM_CERTS", "1") == "0":
        return
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # not installed, or an old system: keep requests' own list
        pass


def main(argv=None):
    _system_certificates()
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
    p.add_argument("--with-crs", action="store_true", help="also fetch CR cover pages")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("fetch-crs", help="download CR cover pages (reason, summary, clauses affected)")
    p.add_argument("specs", nargs="+")
    p.add_argument("--version", action="append", help="only CRs of these new versions, repeatable")
    p.add_argument("--retry", action="store_true", help="retry CRs previously not found")
    p.set_defaults(func=cmd_fetch_crs)

    p = sub.add_parser("fetch-features", help="download the TR 38.822 UE feature list")
    p.set_defaults(func=cmd_fetch_features)

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
