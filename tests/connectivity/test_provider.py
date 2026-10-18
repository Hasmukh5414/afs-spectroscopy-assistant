"""Explicit opt-in only; never contact a provider from the default test suite."""
import os

import pytest

from afs_assistant.config import Settings
from afs_assistant.core.gateway import Gateway


@pytest.mark.connectivity
@pytest.mark.skipif(os.getenv("AFS_TEST_LIVE") != "1", reason="Set AFS_TEST_LIVE=1 to enable real provider calls")
def test_approved_embedding_and_structured_answer_services():
    settings = Settings.from_env()
    with Gateway(settings) as gateway:
        result = gateway.check_services()
    assert result["embeddings"] == "ok" and result["structured_answers"] == "ok"
    if settings.embedding_dimension is not None:
        assert result["dimension"] == settings.embedding_dimension
