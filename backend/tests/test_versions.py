import pytest

from specview.versions import Version, parse_archive_name


@pytest.mark.parametrize("code,text", [("f00", "15.0.0"), ("g10", "16.1.0"), ("hb0", "17.11.0"),
                                       ("i23", "18.2.3"), ("361000", "36.10.0")])
def test_code_roundtrip(code, text):
    v = Version.from_code(code)
    assert str(v) == text
    assert v.code == code


def test_archive_name():
    assert parse_archive_name("38211-g10.zip") == ("38.211", Version(16, 1, 0))
    assert parse_archive_name("index.html") is None


def test_ordering():
    assert Version.parse("16.10.0") > Version.parse("16.9.0") > Version.parse("15.12.0")
