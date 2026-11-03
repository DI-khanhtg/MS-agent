"""Explicitly opt-in; uses only local Docker images/fixtures and never cloud services."""

import os
import re
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.tools.document_reader import extract_document
from tests.fixtures.document_corpus import Fixture, readable_corpus

ROOT = Path(__file__).parents[1] / "fixtures" / "legacy"
CORPUS = readable_corpus() + [
    Fixture(name, (ROOT / name).read_bytes(), (ROOT / (name + ".reference.txt")).read_text(), True)
    for name in ("simple.doc", "basic_test.ppt", "simple.xls")
]


@pytest.mark.document_sandbox
@pytest.mark.parametrize("fixture", CORPUS, ids=lambda fixture: fixture.name)
def test_real_container_conversion_parsing_limits_and_character_agreement(fixture):
    if os.getenv("RUN_DOCUMENT_SANDBOX_TESTS") != "1":
        pytest.skip("Set RUN_DOCUMENT_SANDBOX_TESTS=1 and pin a built Linux document worker image")
    settings = Settings(_env_file=None, document_sandbox_enabled=True)
    result = extract_document(fixture.content, Path(fixture.name).suffix, settings=settings)
    reference = re.sub(r"\s+", " ", fixture.reference).strip()
    actual = re.sub(r"\s+", " ", result.text).strip()
    assert SequenceMatcher(None, reference, actual, autojunk=False).ratio() >= 0.95
    if fixture.converted:
        assert result.metadata["method"] == "libreoffice_conversion"
    assert result.text
