from dataclasses import replace
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from afs_assistant.config import Settings
from afs_assistant.core.models import Corpus, EvidenceItem, EvidenceImage, Locator, Provenance, SourceVersion
from afs_assistant.core.storage import Store
from afs_assistant.indexing.bundle import export_index, install_index
from afs_assistant.retrieval.retriever import Retrieval


def prepared(tmp_path):
    settings = Settings(database=tmp_path/"author.sqlite3", data_dir=tmp_path/"data",
                        embedding_model="same-model", embedding_dimension=2)
    original = b"supplied original"
    digest = hashlib.sha256(original).hexdigest()
    source = SourceVersion(source_id="S01", sha256=digest, title="Methods", uri="urn:afs:S01",
        filename="methods.pdf", kind="application", expected_pages=1, artifact_key="fixture",
        retained_path=f"originals/{digest}/methods.pdf")
    item = EvidenceItem(evidence_id="fiber", source_id="S01", source_version=digest,
        title="Methods", text="Fiber transmission chart", locator=Locator(kind="page", label="PDF p. 1"),
        kind="application", element_type="image", provenance=[Provenance(page=1)],
        image=EvidenceImage(filename="element-1.png"))
    corpus = Corpus(release="shared-test", sources=[source], items=[item],
        processing={"tokenizer": "retained", "vision": {"endpoint": "https://private.invalid", "api_key": "secret-value", "model": "vision"}})
    for name, data in ((source.retained_path, original), ("processed/fixture/page-1.png", b"page"),
                       ("processed/fixture/element-1.png", b"crop"), ("guidance/organizer-guide.json", b'{"text":"guidance"}')):
        path = settings.data_dir/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    store = Store(settings.database)
    store.publish(corpus.release, "original-digest", corpus.model_dump_json(), "same-model", [[0.5, 0.8]])
    store.create_case("private-owner", "PRIVATE CUSTOMER CONVERSATION")
    return settings


def test_roundtrip_retains_vectors_sources_and_assets_without_chats_or_connections(tmp_path):
    author = prepared(tmp_path)
    archive = tmp_path/"shared.zip"
    report = export_index(author, archive)
    assert report["records"] == 1
    recipient = replace(author, database=tmp_path/"recipient/index.sqlite3", data_dir=tmp_path/"recipient/assets")
    installed = install_index(recipient, archive)
    assert installed["document_embedding_calls"] == 0
    snapshot = Store(recipient.database).corpus()
    assert snapshot and json.loads(snapshot["vectors"]) == [[0.5, 0.8]]
    assert "private.invalid" not in snapshot["corpus"] and "secret-value" not in snapshot["corpus"]
    assert json.loads(snapshot["corpus"])["processing"]["tokenizer"] == "retained"
    with Store(recipient.database).connection() as db:
        assert db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
    assert (recipient.data_dir/"processed/fixture/element-1.png").read_bytes() == b"crop"
    assert Retrieval(snapshot).items[0].evidence_id == "fiber"
    with ZipFile(archive) as bundle:
        assert b"PRIVATE CUSTOMER CONVERSATION" not in bundle.read("seed.sqlite3")
        assert not any("checkpoint" in name or ".env" in name for name in bundle.namelist())


def test_install_preserves_existing_database_and_rejects_different_query_model(tmp_path):
    author = prepared(tmp_path)
    archive = tmp_path/"shared.zip"
    export_index(author, archive)
    with pytest.raises(ValueError, match="already exists"):
        install_index(author, archive)
    recipient = replace(author, database=tmp_path/"new.sqlite3", embedding_model="different-model")
    with pytest.raises(ValueError, match="model mismatch"):
        install_index(recipient, archive)
    assert not recipient.database.exists()


def test_corruption_is_rejected_before_installing_any_assets(tmp_path):
    author = prepared(tmp_path)
    archive = tmp_path/"shared.zip"
    export_index(author, archive)
    damaged = tmp_path/"damaged.zip"
    with ZipFile(archive) as source, ZipFile(damaged, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"changed" if name.endswith("element-1.png") else source.read(name))
    recipient = replace(author, database=tmp_path/"recipient/index.sqlite3", data_dir=tmp_path/"recipient/data")
    with pytest.raises(ValueError, match="checksum"):
        install_index(recipient, damaged)
    assert not recipient.database.exists() and not recipient.data_dir.exists()


def test_member_cannot_escape_data_directory(tmp_path):
    author = prepared(tmp_path)
    archive = tmp_path/"shared.zip"
    export_index(author, archive)
    bad = tmp_path/"unsafe.zip"
    with ZipFile(archive) as source, ZipFile(bad, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        payload = b"escape"
        manifest["files"]["data/../../escaped"] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        for name in source.namelist():
            if name != "manifest.json":
                target.writestr(name, source.read(name))
        target.writestr("data/../../escaped", payload)
        target.writestr("manifest.json", json.dumps(manifest))
    recipient = replace(author, database=tmp_path/"recipient/index.sqlite3", data_dir=tmp_path/"recipient/data")
    with pytest.raises(ValueError, match="Unsafe"):
        install_index(recipient, bad)
    assert not recipient.database.exists() and not (tmp_path/"escaped").exists()
