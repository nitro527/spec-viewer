import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


@pytest.fixture(scope="session")
def library(tmp_path_factory):
    from tests_backend_fixture import build_library
    return build_library(tmp_path_factory)
