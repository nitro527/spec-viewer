import shutil

import pytest

from tests.fixtures.make_fixture import write_all

needs_pandoc = pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")


@pytest.fixture(scope="session")
def fixture_docs(tmp_path_factory):
    return write_all(tmp_path_factory.mktemp("docs"))


@pytest.fixture(scope="session")
def library(fixture_docs, tmp_path_factory):
    from specview.parse_docx import parse_docx
    from specview.store import Library
    from specview.versions import parse_archive_name

    lib = Library(tmp_path_factory.mktemp("home") / "library.sqlite")
    for p in fixture_docs:
        spec, version = parse_archive_name(p.name)
        lib.add_version(spec, version, parse_docx(p), recompute=False)
    lib.recompute_history("99.211")
    return lib
