import hashlib
import json
from pathlib import Path

from afs_assistant.config import Settings
from afs_assistant.indexing.ingestion import load_materials


def test_relative_settings_are_anchored_to_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path.parent)
    settings = Settings.from_env(None)
    assert settings.database == tmp_path / "var/afs.sqlite3"
    assert settings.data_dir == tmp_path / "data"


def test_receipt_relocation_retains_hash_and_scope_checks(tmp_path):
    content = b"%PDF-retained-test"
    digest = hashlib.sha256(content).hexdigest()
    directory = tmp_path / "originals" / digest
    directory.mkdir(parents=True)
    original = directory / "source.pdf"
    original.write_bytes(content)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps([{"status": "acquired_unreviewed", "path": "/removed/repo/source.pdf",
        "sha256": digest, "source_id": "S01", "title": "Retained source", "url": "local",
        "role": "guidance", "expected_pages": 1}]))
    assert load_materials(receipt, tmp_path)[0].path == original
    original.write_bytes(b"%PDF-tampered")
    import pytest
    with pytest.raises(ValueError, match="identity"):
        load_materials(receipt, tmp_path)
