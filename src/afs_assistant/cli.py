import argparse
from contextlib import nullcontext
import json
from pathlib import Path
from afs_assistant.config import Settings, PROJECT_ROOT
from afs_assistant.core.gateway import Gateway
from afs_assistant.indexing.publication import ingest
from afs_assistant.core.storage import Store


def main():
    parser = argparse.ArgumentParser(description="AFS spectroscopy assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    loader = sub.add_parser("ingest", help="Ingest normalized JSON evidence")
    loader.add_argument("path", type=Path)
    sub.add_parser("demo-data", help="Load clearly labelled synthetic fixture evidence")
    sub.add_parser("check-services", help="Verify configured embedding and JSON answer services with neutral text")
    export = sub.add_parser("export-index", help="Export the active runtime index and source assets without personal data")
    export.add_argument("path", type=Path)
    install = sub.add_parser("install-index", help="Install a shared runtime index without re-embedding documents")
    install.add_argument("path", type=Path)
    official = sub.add_parser("prepare-official", help="Prepare and verify supplied AFS materials without replacing the active index")
    official.add_argument("--materials", type=Path, default=PROJECT_ROOT / "data/materials")
    official.add_argument("--release", required=True)
    official.add_argument("--embed", action="store_true")
    official.add_argument("--describe-images", action="store_true")
    official.add_argument("--publish", action="store_true", help="Activate only after all coverage checks pass")
    processor = sub.add_parser("process-materials", help="Run source snapshots through Docling, chunks and optional embeddings")
    processor.add_argument("--receipt", type=Path, help="Acquisition receipt; defaults to the latest under data/originals")
    processor.add_argument("--release", required=True, help="New immutable evidence release ID")
    processor.add_argument("--embed", action="store_true", help="Explicitly send evidence chunks to the configured embedding service")
    processor.add_argument("--describe-images", action="store_true", help="Describe retained image crops with AFS_VISION_MODEL before chunk embedding")
    search = sub.add_parser("search", help="Inspect retrieval independently of assessment generation")
    search.add_argument("query")
    search.add_argument("--mode", choices=["lexical", "hybrid"], default="lexical")
    search.add_argument("--limit", type=int, default=5, choices=range(1, 11))
    server = sub.add_parser("serve", help="Serve the local UI and API on loopback")
    server.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command in {"export-index", "install-index"}:
        from afs_assistant.indexing.bundle import export_index, install_index
        try:
            result = export_index(settings, args.path) if args.command == "export-index" else install_index(settings, args.path)
            print(json.dumps(result, indent=2))
        except (ValueError, OSError) as exc:
            parser.exit(1, str(exc) + "\n")
    elif args.command == "prepare-official":
        from afs_assistant.indexing.official import prepare_official
        with Gateway(settings) if args.embed or args.describe_images else nullcontext(None) as gateway:
            report = prepare_official(settings, Store(settings.database), gateway, args.materials, args.release,
                embed=args.embed, describe=args.describe_images, publish_release=args.publish)
        print(json.dumps(report, indent=2))
        if report["status"] == "failed":
            parser.exit(1, "Preparation failed; the previous active release was preserved.\n")
    elif args.command == "process-materials":
        try:
            from afs_assistant.indexing.builder import EvidenceBuilder
            receipts = sorted((settings.data_dir / "originals").glob("acquisition-*.json"))
            if args.receipt is None and not receipts:
                parser.error("Acquire source PDFs first, or supply an acquisition receipt")
            receipt = args.receipt or receipts[-1]
            if args.embed and not settings.embedding_model:
                parser.error("AFS_EMBEDDING_MODEL must be configured for --embed")
            if args.describe_images and not settings.vision_model:
                parser.error("AFS_VISION_MODEL must be configured for --describe-images")
            with Gateway(settings) if args.embed or args.describe_images else nullcontext(None) as gateway:
                report = EvidenceBuilder(settings, Store(settings.database), gateway).process_materials(
                    receipt, args.release, generate_embeddings=args.embed, describe_images=args.describe_images)
            print(json.dumps(report, indent=2))
            if report["status"] == "failed":
                parser.exit(1, "Processing failed; no release was published. Inspect the source states under data/runs.\n")
        except ImportError:
            parser.exit(1, "Install document processing dependencies: uv sync --extra ingestion\n")
    elif args.command == "search":
        from afs_assistant.core.models import SearchRequest
        from afs_assistant.retrieval.retriever import Retrieval
        snapshot = Store(settings.database).corpus()
        if snapshot is None:
            parser.error("No evidence release is published")
        with Gateway(settings) if args.mode == "hybrid" else nullcontext(None) as gateway:
            result = Retrieval(snapshot, gateway, settings).search(SearchRequest(query=args.query, limit=args.limit))
        print(json.dumps(result, indent=2))
    elif args.command == "check-services":
        if not settings.model or not settings.embedding_model:
            parser.error("Configure AFS_MODEL and AFS_EMBEDDING_MODEL first")
        try:
            with Gateway(settings) as gateway:
                print(json.dumps(gateway.check_services(), indent=2))
        except Exception as exc:
            parser.exit(1, f"Service check failed ({type(exc).__name__}). Verify endpoints, credentials, model IDs and dimensions.\n")
    elif args.command == "serve":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, workers=1)
    else:
        path = args.path if args.command == "ingest" else Path(__file__).with_name("fixtures") / "demo.json"
        if args.command == "demo-data" and settings.mode != "demo":
            parser.error("Load demo data with AFS_MODE=demo. Real mode should use an approved corpus.")
        with Gateway(settings) if settings.mode == "openai" else nullcontext(None) as gateway:
            print(json.dumps(ingest(path, Store(settings.database), gateway), indent=2))


if __name__ == "__main__":
    main()

