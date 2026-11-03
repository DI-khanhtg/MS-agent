import re
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from enterprise_agent.tools.document_formats import DocumentLimits
from enterprise_agent.tools.document_worker import process_document
from tests.fixtures.document_corpus import readable_corpus

CORPUS = [fixture for fixture in readable_corpus() if not fixture.converted]


@pytest.mark.parametrize("fixture", CORPUS, ids=lambda fixture: fixture.name)
def test_readable_fixture_character_agreement_at_least_95_percent(fixture, tmp_path):
    result = process_document(
        fixture.content, Path(fixture.name).suffix, DocumentLimits(), tmp_path
    )

    def normalize(text):
        return re.sub(r"\s+", " ", text).strip()

    agreement = SequenceMatcher(
        None, normalize(fixture.reference), normalize(result.text), autojunk=False
    ).ratio()
    assert agreement >= 0.95, (fixture.name, agreement, result.text)
    if result.format in {"eml", "msg"}:
        assert result.metadata["trust"] == "untrusted_document_data"


def test_corpus_has_at_least_twenty_independent_reference_fixtures():
    assert len(CORPUS) >= 20
    assert len({fixture.name for fixture in CORPUS}) == len(CORPUS)
