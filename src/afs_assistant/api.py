from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
from pathlib import Path
import secrets
from urllib.parse import urlsplit
import anthropic
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from afs_assistant.workflow.chat import AnswerValidationError, ChatResponder
from afs_assistant.config import Settings
from afs_assistant.core.models import ChatQuestion, Corpus, NewChat, RevisionRequest
from afs_assistant.core.gateway import Gateway
from afs_assistant.retrieval.retriever import Retrieval
from afs_assistant.core.storage import Store, Conflict, NotFound
from afs_assistant.core.reporting import assessment_markdown
from afs_assistant.core.source_policy import caption_figure


logger = logging.getLogger(__name__)


def chat_failure_code(exc):
    # anthropic.APITimeoutError subclasses APIConnectionError, so timeouts are checked first.
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, anthropic.APITimeoutError)):
        return "timeout"
    if isinstance(exc, (httpx.HTTPStatusError, anthropic.APIStatusError)):
        return "provider_rejected"
    if isinstance(exc, (httpx.RequestError, anthropic.APIConnectionError)):
        return "connection"
    if isinstance(exc, AnswerValidationError):
        return "answer_validation"
    return "internal"


def create_app(settings=None):
    settings = settings or Settings.from_env()
    store = Store(settings.database)
    gateway = Gateway(settings) if settings.mode == "openai" else None
    answers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="afs-answer")

    @asynccontextmanager
    async def lifespan(app):
        # Single process only; recover interrupted requests following a restart.
        store.recover_interrupted()
        snapshot = store.corpus()
        if snapshot:
            Retrieval(snapshot, settings=settings)
        try:
            yield
        finally:
            answers.shutdown(wait=True, cancel_futures=False)
            if gateway is not None:
                gateway.close()

    app = FastAPI(title="AFS Spectroscopy Assistant", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def secure_headers(request: Request, call_next):
        if request.method in {"POST", "PATCH", "PUT"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site requests rejected"}, status_code=403)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "JSON requests required"}, status_code=415)
            # Bound streamed input as well as declared Content-Length.
            size = 0
            pieces = []
            async for piece in request.stream():
                size += len(piece)
                if size > 32000:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
                pieces.append(piece)
            request._body = b"".join(pieces)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotFound)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    def owner(request: Request, response: Response):
        token = request.cookies.get("afs_session", "")
        if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
            token = secrets.token_hex(32)
            response.set_cookie("afs_session", token, httponly=True, samesite="strict", max_age=2592000)
        # Never persist the bearer cookie itself.
        return hashlib.sha256(token.encode()).hexdigest()

    @app.get("/api/health")
    def health():
        snapshot = store.corpus()
        return {"status": "ok", "mode": settings.mode, "release": snapshot["id"] if snapshot else None,
                "evidence_ready": bool(snapshot), "scope": "local expert-assessment prototype"}

    @app.get("/api/chats")
    def chats(identity=Depends(owner)):
        return store.list_cases(identity)

    def answer_turn(case_id, turn_id, identity, release):
        chat = store.get_chat(case_id, identity)
        history = []
        if not chat["turns"] or chat["turns"][0]["text"] != chat["text"]:
            history.append({"role":"user", "content":chat["text"]})
        for turn in chat["turns"]:
            history.append({"role":"user", "content":turn["text"]})
            if turn["answer"] and turn["release"] == release:
                history.append({"role":"assistant", "content":"\n\n".join(part["text"] for part in turn["answer"]["parts"])})
        responder = None
        try:
            responder = ChatResponder(Retrieval(store.corpus(release), gateway, settings), settings, gateway,
                thread_id=f"{identity}:{case_id}:{turn_id}:{release}",
                progress=lambda trace: store.progress_chat_turn(turn_id, trace))
            answer, sources = responder.answer(history)
            store.finish_chat_turn(turn_id, answer, sources, responder.trace)
        except Exception as exc:
            code = chat_failure_code(exc)
            trace = responder.trace if responder is not None else []
            failure = {"stage":"failed", "error_type":type(exc).__name__, "code":code}
            if isinstance(exc, AnswerValidationError):
                # Only our own validator messages; provider errors can contain private service details.
                failure["reason"] = str(exc.__cause__ or exc)[:300]
            trace.append(failure)
            logger.warning("Chat turn %s failed: %s (%s)", turn_id, code, type(exc).__name__)
            store.finish_chat_turn(turn_id, None, {}, trace, status="failed")
        return store.get_chat(case_id, identity)

    def dispatch_answer(case_id, turn_id, identity, release):
        if gateway is not None and settings.background_answers:
            answers.submit(answer_turn, case_id, turn_id, identity, release)
            return store.get_chat(case_id, identity)
        return answer_turn(case_id, turn_id, identity, release)

    def send_chat_question(case_id, identity, expected, text, request_id):
        if not text.strip():
            raise HTTPException(422, "Write a question first.")
        snapshot = store.corpus()
        if not snapshot:
            raise HTTPException(503, "No documents are indexed yet.")
        turn_id, created = store.start_chat_turn(case_id, identity, expected, text, request_id, snapshot["id"])
        if not created:
            return store.get_chat(case_id, identity)
        return dispatch_answer(case_id, turn_id, identity, snapshot["id"])

    @app.post("/api/chats", status_code=201)
    def new_chat(body: NewChat, identity=Depends(owner)):
        if not body.text.strip():
            raise HTTPException(422, "Write a question first.")
        case = store.create_case(identity, body.text, request_id=body.request_id)
        return send_chat_question(case["id"], identity, case["revision"], body.text, body.request_id)

    @app.get("/api/chats/{case_id}")
    def get_chat(case_id: str, identity=Depends(owner)):
        return store.get_chat(case_id, identity)

    @app.post("/api/chats/{case_id}/messages")
    def chat_message(case_id: str, body: ChatQuestion, identity=Depends(owner)):
        # Ownership is checked before accessing evidence or invoking the provider.
        store.get_chat(case_id, identity)
        return send_chat_question(case_id, identity, body.expected_revision, body.text, body.request_id)

    @app.post("/api/chats/{case_id}/turns/{turn_id}/retry")
    def retry_answer(case_id: str, turn_id: str, body: RevisionRequest, identity=Depends(owner)):
        release = store.retry_chat_turn(case_id, turn_id, identity, body.expected_revision)
        return dispatch_answer(case_id, turn_id, identity, release)

    def chat_source(case_id, turn_id, evidence_id, identity):
        chat = store.get_chat(case_id, identity)
        turn = next((turn for turn in chat["turns"] if turn["id"] == turn_id), None)
        if turn is None or evidence_id not in turn["sources"]:
            raise NotFound("Source not cited in this answer")
        item = dict(turn["sources"][evidence_id])
        snapshot = store.corpus(turn["release"])
        if snapshot is None:
            raise NotFound("The cited document release is unavailable")
        corpus = Corpus.model_validate_json(snapshot["corpus"])
        source = next((source for source in corpus.sources if source.source_id == item["source_id"]), None)
        page = item.get("related_page") or min((position["page"] for position in item.get("provenance", [])), default=None)
        page_source = next((s for s in corpus.sources if s.source_id == item.get("related_source_id")), source)
        page_file = None
        image_file = None
        figure = caption_figure(item, corpus.items)
        if figure is not None:
            item["display_figure"] = {"evidence_id": figure.evidence_id,
                "locator": figure.locator.model_dump(), "image": figure.image.model_dump()}
        image_metadata = item.get("image") or (item.get("display_figure") or {}).get("image")
        if page_source is not None and page_source.artifact_key and page is not None:
            root = (settings.data_dir / "processed").resolve()
            candidate = (root / page_source.artifact_key / f"page-{page}.png").resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                page_file = candidate
        if source is not None and source.artifact_key and image_metadata:
            root = (settings.data_dir / "processed").resolve()
            candidate = (root / source.artifact_key / image_metadata["filename"]).resolve()
            if candidate.is_relative_to(root) and candidate.is_file() and candidate.suffix == ".png":
                image_file = candidate
        uri = source.uri if source is not None else ""
        parsed = urlsplit(uri)
        original = uri.split("#")[0] + (f"#page={page}" if page else "") if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else None
        if source is not None and source.retained_path:
            original = f"/api/chats/{case_id}/turns/{turn_id}/sources/{evidence_id}/original"
            if source.format == "pdf" and page:
                original += f"#page={page}"
        return item, original, page_file, image_file

    @app.get("/api/chats/{case_id}/turns/{turn_id}/sources/{evidence_id}/original")
    def cited_original(case_id: str, turn_id: str, evidence_id: str, identity=Depends(owner)):
        item, _, _, _ = chat_source(case_id, turn_id, evidence_id, identity)
        chat = store.get_chat(case_id, identity)
        turn = next(t for t in chat["turns"] if t["id"] == turn_id)
        snapshot = store.corpus(turn["release"])
        if snapshot is None:
            raise NotFound("The cited document release is unavailable")
        corpus = Corpus.model_validate_json(snapshot["corpus"])
        source = next((s for s in corpus.sources if s.source_id == item["source_id"]), None)
        if source is None or not source.retained_path:
            raise NotFound("Retained original is unavailable")
        root = (settings.data_dir / "originals").resolve()
        path = (settings.data_dir / source.retained_path).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source.sha256:
            raise NotFound("Retained original is unavailable or changed")
        media = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "html": "application/octet-stream"}[source.format]
        return FileResponse(path, media_type=media, filename=source.filename,
            content_disposition_type="inline" if source.format == "pdf" else "attachment")

    @app.get("/api/chats/{case_id}/turns/{turn_id}/export")
    def export_answer(case_id: str, turn_id: str, format: str = "json", identity=Depends(owner)):
        chat = store.get_chat(case_id, identity)
        turn = next((t for t in chat["turns"] if t["id"] == turn_id), None)
        if turn is None or not turn["answer"]:
            raise NotFound("A completed answer is required")
        if format not in {"json", "md"}:
            raise HTTPException(422, "Choose json or md")
        payload = {"question": turn["text"], "release": turn["release"], "created": turn["created"],
            "answer": turn["answer"], "sources": turn["sources"],
            "generation": next((e for e in reversed(turn["trace"] or []) if e.get("stage") == "completed"), None)}
        headers = {"Content-Disposition": f'attachment; filename="afs-assessment-{turn_id}.{format}"'}
        if format == "json":
            return JSONResponse(payload, headers=headers)
        return Response(assessment_markdown(payload), media_type="text/markdown", headers=headers)

    @app.get("/api/chats/{case_id}/turns/{turn_id}/sources/{evidence_id}")
    def cited_source(case_id: str, turn_id: str, evidence_id: str, identity=Depends(owner)):
        item, original, page_file, image_file = chat_source(case_id, turn_id, evidence_id, identity)
        base = f"/api/chats/{case_id}/turns/{turn_id}/sources/{evidence_id}"
        return {"evidence":item, "original_url":original, "page_url":base+"/page" if page_file else None,
                "image_url":base+"/image" if image_file else None}

    @app.get("/api/chats/{case_id}/turns/{turn_id}/sources/{evidence_id}/page")
    def cited_page(case_id: str, turn_id: str, evidence_id: str, identity=Depends(owner)):
        _, _, page_file, _ = chat_source(case_id, turn_id, evidence_id, identity)
        if page_file is None:
            raise NotFound("Document page is not available locally")
        return FileResponse(page_file, media_type="image/png")

    @app.get("/api/chats/{case_id}/turns/{turn_id}/sources/{evidence_id}/image")
    def cited_image(case_id: str, turn_id: str, evidence_id: str, identity=Depends(owner)):
        _, _, _, image_file = chat_source(case_id, turn_id, evidence_id, identity)
        if image_file is None:
            raise NotFound("This cited chunk has no retained image crop")
        return FileResponse(image_file, media_type="image/png")

    web = Path(__file__).with_name("web")
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    return app

