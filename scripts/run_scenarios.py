"""Run organizer inputs through the same chat API; preserve generated outputs."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
from contextlib import ExitStack
from tempfile import TemporaryDirectory
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient

from afs_assistant.api import create_app
from afs_assistant.config import Settings, PROJECT_ROOT
from afs_assistant.core.storage import Store


def scenarios(path):
    if path.suffix == ".txt":
        text = path.read_text(encoding="utf-8")
        matches = list(re.finditer(r"^(H\d{2})\s*\|\s*(.+)$", text, re.M))
        return [(match[1], match[2], re.sub(r"^=+\s*$", "", text[match.end():
            matches[n+1].start() if n+1 < len(matches) else len(text)], flags=re.M).strip())
            for n, match in enumerate(matches)]
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = ["".join(t.text or "" for t in p.findall(".//w:t", ns))
        for p in root.findall(".//w:p", ns)]
    found = {}
    for n, text in enumerate(paragraphs[:-1]):
        match = re.match(r"^(D\d{2})\s+·\s+(.+)$", text)
        if match and match[1] not in found:
            found[match[1]] = (match[1], match[2], paragraphs[n+1])
    return list(found.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", choices=["development", "final"], default="development")
    parser.add_argument("--materials", type=Path, default=PROJECT_ROOT/"data/materials")
    parser.add_argument("--only", nargs="+", help="Optional scenario IDs; never changes their text")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT/"var/scenario-runs"/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    args = parser.parse_args()
    filename = "AFS_Examples_and_Reference.docx" if args.set == "development" else "customer_scenarios_for_final_evaluation.txt"
    path = args.materials/filename
    inputs = scenarios(path)
    expected = 8 if args.set == "development" else 6
    if len(inputs) != expected:
        parser.error("Organizer input count differs from the supplied bundle")
    selected = [row for row in inputs if not args.only or row[0] in args.only]
    if not selected or (args.only and set(args.only) != {row[0] for row in selected}):
        parser.error("Unknown scenario ID")
    settings = replace(Settings.from_env(), background_answers=False)
    if settings.mode != "openai":
        parser.error("Configure real model mode; synthetic demo answers are not submission outputs")
    output = args.output/args.set
    output.mkdir(parents=True, exist_ok=True)
    failed = False
    snapshot = Store(settings.database).corpus()
    if snapshot is None:
        parser.error("Publish the official index before running scenarios")
    # Reuse the exact published snapshot and normal API, but keep test chats and
    # restart recovery separate from any browser session/server already running.
    with ExitStack() as stack:
        work = Path(stack.enter_context(TemporaryDirectory(prefix="afs-scenarios-")))
        isolated = replace(settings, database=work/"scenarios.sqlite3")
        Store(isolated.database).publish(snapshot["id"], snapshot["digest"], snapshot["corpus"],
            snapshot["model"], json.loads(snapshot["vectors"]))
        client = stack.enter_context(TestClient(create_app(isolated)))
        for sid, title, text in selected:
            print(f"{sid}: running", flush=True)
            response = client.post("/api/chats", json={"text": text, "request_id": uuid4().hex})
            response.raise_for_status()
            chat = response.json()
            turn = chat["turns"][-1]
            if turn["status"] != "answered" or turn["answer"].get("assessment") is None:
                failed = True
                for suffix in (".json", ".md"):
                    (output/f"{sid}{suffix}").unlink(missing_ok=True)
                (output/f"{sid}-failure.json").write_text(json.dumps({
                    "id": sid, "question": text, "status": turn["status"], "trace": turn["trace"]}, indent=2), encoding="utf-8")
                print(f"{sid}: failed or missing structured assessment", flush=True)
                continue
            base = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export'
            payload = client.get(base).json()
            payload["organizer_input"] = {"id": sid, "title": title, "filename": path.name,
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "question_sha256": hashlib.sha256(text.encode()).hexdigest()}
            (output/f"{sid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            (output/f"{sid}.md").write_text(client.get(base+"?format=md").text, encoding="utf-8")
            (output/f"{sid}-failure.json").unlink(missing_ok=True)
            print(f"{sid}: saved unchanged generated answer", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
