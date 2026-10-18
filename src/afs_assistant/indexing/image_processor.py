"""Describe retained crops once, then enrich their independent search chunks."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import re
from typing import Callable
from threading import Lock

from afs_assistant.config import Settings
from afs_assistant.core.fingerprints import fingerprint
from afs_assistant.core.gateway import Gateway, VisionTokenLimitError
from afs_assistant.core.models import ChunkBatch, EvidenceItem, ExtractedDocument, ImageDescription
from afs_assistant.core.source_policy import visual_search_text
from .chunking import indexes_nearby_text
from .material_cache import MaterialCache

IMAGE_PROCESSOR_VERSION = "afs-vision-v1"
IMAGE_CHUNK_VERSION = "afs-vision-chunks-v3"


def validate_description(text: str) -> None:
    if not 30 <= len(text.strip()) <= 6000:
        raise ValueError("Image description is empty, too short or too long")
    if re.search(r"\b(\w+)(?:\s+\1){3,}\b|\b(\w+\s+\w+)(?:\s+\2){2,}\b", text, re.IGNORECASE):
        raise ValueError("Image description contains a repetition loop")
    if re.search(r"\b(?:cannot|can't|unable to) (?:view|see|access|process) (?:the |this |any )?(?:image|picture)", text, re.IGNORECASE):
        raise ValueError("Vision service could not inspect the image")


class ImageProcessor:
    def __init__(self, settings: Settings, cache: MaterialCache, gateway: Gateway):
        self.settings, self.cache, self.gateway = settings, cache, gateway
        self.prompt = (Path(__file__).parent / "prompts" / "describe_image.md").read_text(encoding="utf-8")
        self.prompt_hash = hashlib.sha256(self.prompt.encode()).hexdigest()
        self.profile = {"version": IMAGE_PROCESSOR_VERSION, "model": settings.vision_model,
                        "endpoint": settings.vision_api_base or settings.api_base,
                        "reasoning": settings.vision_reasoning_effort, "prompt_sha256": self.prompt_hash,
                        "temperature": 0.2, "max_tokens": 1200}
        self._locks: dict[str, Lock] = {}
        self._locks_guard = Lock()
        self.fallback_policy = {"on": "output_token_limit", "reasoning_effort": "none", "max_retries": 1}

    def enrich(self, batch: ChunkBatch, document: ExtractedDocument,
               progress: Callable[[dict], None], count_tokens: Callable[[str], int]) -> ChunkBatch:
        result = batch.model_copy(deep=True)
        images = [item for item in result.items if item.image is not None]
        counts = {"total": len(images), "completed": 0, "generated": 0, "cached": 0}
        progress(dict(counts))
        directory = self.cache.artifact_directory(document.source.sha256, document.processing_key).resolve()
        executor = ThreadPoolExecutor(max_workers=self.settings.vision_workers)
        index_nearby = indexes_nearby_text(document.source)
        futures = {executor.submit(self._enrich_item, item, directory, index_nearby): item for item in images}
        try:
            for future in as_completed(futures):
                generated = future.result()
                item = futures[future]
                item.oversized = count_tokens(item.text) > self.settings.chunk_size
                counts["generated" if generated else "cached"] += 1
                counts["completed"] += 1
                progress(dict(counts))
        finally:
            # Preserve completed cache entries; stop queued calls after an error.
            executor.shutdown(wait=True, cancel_futures=True)
        return result

    def _enrich_item(self, item: EvidenceItem, directory: Path, index_nearby: bool = True) -> bool:
        image = item.image
        if image is None:
            raise ValueError("Image chunk is missing its crop metadata")
        path = (directory / image.filename).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Image crop must stay within its document directory")
        pixels = path.read_bytes()
        if not pixels.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Image crop is not a PNG")
        image_hash = hashlib.sha256(pixels).hexdigest()
        context = f"Document: {item.title}\nCaption: {image.caption or '[not supplied]'}\nNearby text: {image.nearby_text}"
        request_profile = dict(self.profile)
        if len(pixels) > 600_000:
            request_profile["image_transport"] = Gateway.VISION_TRANSPORT
        key = fingerprint({"profile": request_profile, "image_sha256": image_hash, "context": context})
        with self._locks_guard:
            lock = self._locks.setdefault(key, Lock())
        with lock:
            description, generated = self._description(key, pixels, context, image_hash, request_profile)
        image.description = description
        image.interpretation = "vision_description_unreviewed"
        image.review_notes = []
        search_text, omitted_readings = visual_search_text(description.text)
        if omitted_readings:
            image.review_notes.append("Numerical curve-position statements from this AI description are excluded from search and answer context; inspect the original plot for its values.")
        item.text = (f"Image from {item.title}.\nCaption: {image.caption or '[not supplied]'}\n"
                     f"AI-generated visual description (unreviewed): {search_text}")
        if index_nearby:
            # The vision request still receives nearby text; only the indexed text omits slide OCR.
            item.text += f"\nNearby document text (context, not visual observation): {image.nearby_text or '[not supplied]'}"
        if image.review_notes:
            item.text += "\nVisual review notes: " + " ".join(image.review_notes)
        item.evidence_id = item.source_id[:40] + "_" + fingerprint({"version": IMAGE_CHUNK_VERSION,
            "base": item.evidence_id, "description_key": description.cache_key, "text": item.text})[:40]
        item.qualifications = [note for note in item.qualifications if "pixels" not in note and "interpreted" not in note]
        item.qualifications.append("AI-generated image description is unreviewed; inspect the crop before relying on visual claims.")
        item.qualifications.extend(image.review_notes)
        return generated

    def _description(self, key: str, pixels: bytes, context: str, image_hash: str, request_profile=None) -> tuple[ImageDescription, bool]:
        effort = self.settings.vision_reasoning_effort
        candidates: list[tuple[str, str, str | None]] = [(key, effort, None)]
        if effort != "none":
            fallback_profile = {**(request_profile or self.profile), "reasoning": "none", "fallback_from_reasoning": effort}
            fallback_key = fingerprint({"profile": fallback_profile, "image_sha256": image_hash, "context": context})
            candidates.append((fallback_key, "none", effort))
        for candidate_key, candidate_effort, origin in candidates:
            description = self.cache.load_image_description(candidate_key)
            if description is None:
                continue
            validate_description(description.text)
            if (description.image_sha256 != image_hash or description.prompt_sha256 != self.prompt_hash
                    or description.model != self.settings.vision_model):
                raise ValueError("Image description provenance mismatch")
            # Older successful caches predate explicit per-image reasoning metadata.
            if "reasoning_effort" not in description.model_fields_set:
                description.reasoning_effort = candidate_effort
                description.fallback_from_reasoning = origin
                self.cache.save_image_description(description)
            if description.reasoning_effort != candidate_effort or description.fallback_from_reasoning != origin:
                raise ValueError("Image description reasoning provenance mismatch")
            return description, False
        chosen_key, chosen_effort, origin = candidates[0]
        try:
            text = self.gateway.describe_image(pixels, context, self.prompt)
        except VisionTokenLimitError:
            if len(candidates) < 2:
                raise
            chosen_key, chosen_effort, origin = candidates[1]
            text = self.gateway.describe_image(pixels, context, self.prompt, reasoning_effort=chosen_effort)
        validate_description(text)
        description = ImageDescription(text=text, model=self.settings.vision_model,
            prompt_sha256=self.prompt_hash, image_sha256=image_hash, cache_key=chosen_key,
            generated_at=datetime.now(timezone.utc).isoformat(), reasoning_effort=chosen_effort,
            fallback_from_reasoning=origin)
        self.cache.save_image_description(description)
        return description, True
