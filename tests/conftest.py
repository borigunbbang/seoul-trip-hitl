import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
REF = date(2026, 11, 9)


@pytest.fixture
def provider():
    from trip.provider import SnapshotProvider

    return SnapshotProvider(FIXTURES / "snapshot", FIXTURES / "overrides.json", reference_date=REF)


@pytest.fixture
def raw_provider():
    """덮어쓰기를 적용하지 않은 원본."""
    from trip.provider import SnapshotProvider

    return SnapshotProvider(FIXTURES / "snapshot", None, reference_date=REF)
