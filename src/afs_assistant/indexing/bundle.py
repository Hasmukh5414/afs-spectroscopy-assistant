"""Portable runtime index, without service credentials or user conversations."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from afs_assistant.core.embeddings import validate_vectors
from afs_assistant.core.models import Corpus
from afs_assistant.core.storage import Store


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Bundle path escapes its data directory")
    return path


def clean_metadata(value):
    """Remove provider connection metadata while retaining model/provenance IDs."""
    if isinstance(value, dict):
        return {key: clean_metadata(child) for key, child in value.items()
                if not any(word in key.lower() for word in ("endpoint", "api_base", "api_key", "password", "secret", "access_token", "authorization"))}
    if isinstance(value, list):
        return [clean_metadata(child) for child in value]
    return value


def export_index(settings, destination: Path):
    snapshot = Store(settings.database).corpus()
    if not snapshot:
        raise ValueError("Publish an evidence release first")
    corpus = Corpus.model_validate_json(snapshot["corpus"])
    vectors = json.loads(snapshot["vectors"])
    validate_vectors(vectors, len(corpus.items))
    if not vectors or not snapshot["model"]:
        raise ValueError("A shared index must contain real embeddings")
    # The scientific records/vectors are unchanged. Only connection metadata is omitted.
    corpus = Corpus.model_validate({**corpus.model_dump(), "processing": clean_metadata(corpus.processing)})
    canonical = json.dumps(corpus.model_dump(), sort_keys=True, separators=(",", ":"))
    digest = sha((canonical + "\n" + snapshot["model"]).encode())
    assets = {}

    def include(relative):
        path = checked_path(settings.data_dir, relative)
        if not path.is_file():
            raise ValueError(f"Required runtime asset is missing: {relative}")
        assets["data/" + relative] = path

    for source in corpus.sources:
        if source.retained_path:
            include(source.retained_path)
            if sha(assets["data/" + source.retained_path].read_bytes()) != source.sha256:
                raise ValueError("Retained original hash mismatch")
        if source.artifact_key and source.format == "pdf":
            for page in range(1, source.expected_pages + 1):
                include(f"processed/{source.artifact_key}/page-{page}.png")
    by_source = {source.source_id: source for source in corpus.sources}
    for item in corpus.items:
        if item.image:
            include(f"processed/{by_source[item.source_id].artifact_key}/{item.image.filename}")
    guidance = "guidance/organizer-guide.json"
    if (settings.data_dir / guidance).is_file():
        include(guidance)

    destination = Path(destination)
    if destination.exists():
        raise ValueError("Bundle already exists; choose a new output filename")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="afs-export-") as work:
        seed = Path(work) / "seed.sqlite3"
        working = Path(work) / "working.sqlite3"
        Store(working).publish(corpus.release, digest, canonical, snapshot["model"], vectors)
        # SQLite backup captures committed WAL data; the portable copy has no sidecars.
        with closing(sqlite3.connect(working)) as source_db, closing(sqlite3.connect(seed)) as db:
            source_db.backup(db)
            db.execute("PRAGMA journal_mode=DELETE").fetchone()
            db.execute("VACUUM")
        assets["seed.sqlite3"] = seed
        manifest = {"format": "afs-runtime-index-v1", "release": corpus.release,
            "embedding_model": snapshot["model"], "embedding_dimension": len(vectors[0]),
            "records": len(corpus.items), "sources": len(corpus.sources),
            "sanitization": "Provider connection metadata omitted; no chats, sessions, checkpoints or credentials.",
            "files": {name: {"sha256": sha(path.read_bytes()), "bytes": path.stat().st_size}
                      for name, path in sorted(assets.items())}}
        with ZipFile(destination, "x", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for name, path in sorted(assets.items()):
                archive.write(path, name)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    return {key: value for key, value in manifest.items() if key != "files"} | {
        "asset_files": len(assets)-1, "archive_bytes": destination.stat().st_size,
        "archive_sha256": sha(destination.read_bytes())}


def install_index(settings, bundle: Path):
    """Verify everything before writing; never replace an existing local database."""
    if settings.database.exists():
        raise ValueError("Database already exists. Use a fresh AFS_DATABASE path; existing chats/index are preserved.")
    with TemporaryDirectory(prefix="afs-import-") as work, ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "afs-runtime-index-v1":
            raise ValueError("Unsupported index bundle format")
        files = manifest["files"]
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(files) | {"manifest.json"}:
            raise ValueError("Bundle contains missing, duplicate or unexpected files")
        if "seed.sqlite3" not in files:
            raise ValueError("Bundle has no seed database")
        if settings.embedding_model and settings.embedding_model != manifest["embedding_model"]:
            raise ValueError("Use the bundle's embedding model for query embeddings; model mismatch")
        if settings.embedding_dimension and settings.embedding_dimension != manifest["embedding_dimension"]:
            raise ValueError("Embedding dimension differs from the shared index")
        staged = Path(work)
        for name, metadata in files.items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or (
                    name != "seed.sqlite3" and not name.startswith("data/")):
                raise ValueError("Unsafe bundle member path")
            payload = archive.read(name)
            if len(payload) != metadata["bytes"] or sha(payload) != metadata["sha256"]:
                raise ValueError("Bundle file checksum mismatch")
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            if name.startswith("data/"):
                destination = checked_path(settings.data_dir, name[5:])
                if destination.exists() and (not destination.is_file() or sha(destination.read_bytes()) != metadata["sha256"]):
                    raise ValueError("Existing data asset differs; use a fresh AFS_DATA_DIR")
        seed = staged / "seed.sqlite3"
        with closing(sqlite3.connect(f"file:{seed}?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Seed database failed integrity check")
            for table in ("cases", "chat_turns", "chat_starts"):
                if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                    raise ValueError("Seed database contains personal conversation data")
            rows = db.execute("SELECT r.corpus,r.vectors,r.model FROM releases r JOIN current_release c ON r.id=c.id").fetchall()
            if len(rows) != 1 or db.execute("SELECT COUNT(*) FROM releases").fetchone()[0] != 1:
                raise ValueError("Bundle must contain only the active release")
            corpus = Corpus.model_validate_json(rows[0][0])
            if corpus.release != manifest["release"] or len(corpus.items) != manifest["records"] or rows[0][2] != manifest["embedding_model"]:
                raise ValueError("Seed database differs from its manifest")
            validate_vectors(json.loads(rows[0][1]), len(corpus.items), manifest["embedding_dimension"])
        for name in files:
            if name.startswith("data/"):
                destination = checked_path(settings.data_dir, name[5:])
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("xb") as output, (staged / name).open("rb") as source:
                        shutil.copyfileobj(source, output)
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        # Install the DB last, atomically, without overwriting one created meanwhile.
        temporary = settings.database.with_name(settings.database.name + ".installing")
        try:
            with temporary.open("xb") as output, seed.open("rb") as source:
                shutil.copyfileobj(source, output)
            os.link(temporary, settings.database)
        finally:
            temporary.unlink(missing_ok=True)
    return {"release": manifest["release"], "records": manifest["records"],
            "embedding_model": manifest["embedding_model"], "embedding_dimension": manifest["embedding_dimension"],
            "status": "installed", "document_embedding_calls": 0}
