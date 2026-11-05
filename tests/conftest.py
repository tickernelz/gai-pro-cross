import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gai_pro.paths import Layout
from gai_pro.store import Store


@pytest.fixture()
def layout(tmp_path):
    return Layout(str(tmp_path / "data"))


@pytest.fixture()
def store(layout):
    return Store(layout)
