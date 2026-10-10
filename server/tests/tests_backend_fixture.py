"""Builds the backend's synthetic 99.211 library and attaches one parsed CR cover."""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from specview.parse_docx import parse_docx  # noqa: E402
from specview.store import Library  # noqa: E402
from specview.versions import parse_archive_name  # noqa: E402
from tests.fixtures.make_fixture import write_all  # noqa: E402  (backend/tests)


def build_library(tmp_path_factory):
    lib = Library(tmp_path_factory.mktemp("home") / "library.sqlite")
    for p in write_all(tmp_path_factory.mktemp("docs")):
        spec, version = parse_archive_name(p.name)
        lib.add_version(spec, version, parse_docx(p), recompute=False)
    lib.recompute_history("99.211")
    cover = {"cr": "0123", "title": "Corrections for NR-U frame structure", "wi_code": "NR_unlic-Core",
             "category": "F", "reason": "Missing note on shared spectrum.", "summary": "Add a NOTE.",
             "clauses_raw": "4.3.1", "clauses": ["4.3.1"], "wg_tdoc": "R1-2001234", "release": "Rel-16"}
    with lib.db:
        lib.db.execute("INSERT INTO cr_detail VALUES (?,?,?,?,?,?,?)",
                       ("99.211", "0123", "", "RP-200186", "ok", json.dumps(cover), None))
    return lib
