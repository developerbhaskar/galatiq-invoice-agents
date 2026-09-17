import os
import sys

# make the project root importable when pytest runs from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from setup_db import build_db
from config import DB_PATH


@pytest.fixture(scope="session", autouse=True)
def _db():
    build_db(DB_PATH)
    yield
