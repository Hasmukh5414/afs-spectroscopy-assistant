"""Load admitted local source snapshots for document preparation."""
import hashlib
import json
from pathlib import Path

from afs_assistant.core.models import SourceMaterial, SourceVersion


def load_materials(receipt: Path, data_dir: Path) -> list[SourceMaterial]:
    records = json.loads(receipt.read_text())
    if not isinstance(records, list) or not 1 <= len(records) <= 100:
        raise ValueError("Receipt must contain 1..100 source records")
    originals = (data_dir / "originals").resolve()
    materials = []
    ids = set()
    hashes = set()
    for record in records:
        if record.get("status") != "acquired_unreviewed":
            raise ValueError("All receipt sources must have been successfully acquired")
        path = Path(record["path"]).expanduser()
        if not path.is_absolute():
            path = data_dir / path
        if not path.is_file():
            # A verified retained original can survive a checkout relocation.
            # Its hash is still checked below; never trust an arbitrary old path.
            path = originals / record["sha256"] / path.name
        path = path.resolve()
        if not path.is_relative_to(originals) or not path.is_file():
            raise ValueError("Source must be retained under the configured originals directory")
        if path.stat().st_size > 25_000_000:
            raise ValueError("Source exceeds 25 MB")
        content = path.read_bytes()
        fmt = record.get("format", "pdf")
        signature_ok = (fmt == "pdf" and content.startswith(b"%PDF-")) or (fmt == "xlsx" and content.startswith(b"PK")) or (fmt == "html" and bool(content.strip()))
        if not signature_ok or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError("Original PDF identity does not match acquisition receipt")
        source = SourceVersion(source_id=record["source_id"], sha256=record["sha256"], title=record["title"],
            uri=record["url"], filename=path.name, kind=record["role"], expected_pages=record["expected_pages"],
            issue_ids=record.get("issues_from_specification", []), redistribution=record.get("redistribution", "unverified"),
            format=fmt, authority=record.get("authority", "supplementary"),
            evidence_role=record.get("evidence_role", "technical"), visual_strategy=record.get("visual_strategy", "regions"),
            retained_path=str(path.relative_to(data_dir.resolve())), citation_keys=record.get("citation_keys", []),
            structured_table_pages=record.get("structured_table_pages", []), snapshot_at=record.get("snapshot_at", ""))
        if source.source_id in ids or source.sha256 in hashes:
            raise ValueError("Duplicate source ID or original hash; admit each document once")
        ids.add(source.source_id)
        hashes.add(source.sha256)
        materials.append(SourceMaterial(source=source, path=path))
    return materials
