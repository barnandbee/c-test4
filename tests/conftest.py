import pathlib
import sys

import pytest

# Make the repo root importable when running `pytest` from anywhere.
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return FIXTURES


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
