"""Lazy Docling conversion retaining canonical JSON, provenance and visual assets."""
from importlib.metadata import version
from pathlib import Path
import os
import re

from afs_assistant.config import Settings
from afs_assistant.core.fingerprints import fingerprint
from afs_assistant.core.models import DocumentElement, ExtractedDocument, Provenance, SourceMaterial

EXTRACTOR_VERSION = "afs-docling-v3"


class DoclingExtractor:
    def __init__(self, settings: Settings, *, ocr_mode: str = "pdf_aware_layout_regions"):
        self.settings = settings
        self.parser_version = version("docling")
        self.converter = None
        self.ocr_mode = ocr_mode

    def processing_key(self, source_hash: str) -> str:
        return fingerprint({"source": source_hash, "adapter": EXTRACTOR_VERSION,
            "docling": self.parser_version, "docling_core": version("docling-core"),
            "ocr": self.settings.do_ocr, "ocr_mode": self.ocr_mode,
            "ocr_engine": "docling-rapidocr-en-small", "image_scale": self.settings.image_scale,
            "tables": True, "page_images": True, "picture_images": True})

    def extract(self, material: SourceMaterial, directory: Path) -> ExtractedDocument:
        os.environ.setdefault("HF_HOME", str((self.settings.data_dir / "models" / "huggingface").resolve()))
        from docling.datamodel.base_models import ConversionStatus, InputFormat
        from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc.base import ImageRefMode
        from docling_core.types.doc.common.content_layer import ContentLayer
        from docling_core.types.doc.items.node import DocItem
        from docling_core.types.doc.items.picture.picture import PictureItem
        from docling_core.types.doc.items.table.table import TableItem

        if self.converter is None:
            options = PdfPipelineOptions(do_ocr=self.settings.do_ocr, do_table_structure=True,
                generate_page_images=True, generate_picture_images=True, images_scale=self.settings.image_scale,
                generate_parsed_pages=self.settings.do_ocr,
                do_picture_classification=False, layout_batch_size=1, ocr_batch_size=1, table_batch_size=1)
            if self.settings.do_ocr:
                options.ocr_options = RapidOcrOptions(lang=["en"], mode=OcrMode(self.ocr_mode),
                    rapidocr_params={"Global.model_root_dir": str((self.settings.data_dir / "models/rapidocr").resolve()),
                        "Global.log_level": "error"})
            self.converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        result = self.converter.convert(material.path, raises_on_error=False)
        if result.status != ConversionStatus.SUCCESS:
            raise ValueError("Docling conversion was failed or partial; no evidence release can be published")
        document = result.document
        if len(document.pages) != material.source.expected_pages:
            raise ValueError("Parsed page count differs from the admitted source")
        directory.mkdir(parents=True, exist_ok=True)
        pages = {}
        for page_number, page in document.pages.items():
            if page.image is None:
                raise ValueError("Docling omitted a required page image")
            filename = f"page-{page_number}.png"
            page_image = page.image.pil_image
            if page_image is None:
                raise ValueError("Docling page image could not be decoded")
            page_image.save(directory / filename)
            pages[page_number] = filename

        elements = []
        # Keep usable items even when Docling classifies them as furniture;
        # the chunker explicitly excludes actual page headers and footers.
        for index, (item, _) in enumerate(document.iterate_items(included_content_layers=set(ContentLayer))):
            if not isinstance(item, DocItem):
                continue
            label = str(item.label.value)
            text = getattr(item, "text", "")
            caption = ""
            notes = ""
            table_data = {}
            image_path = None
            if isinstance(item, (TableItem, PictureItem)):
                caption = item.caption_text(document)
                notes = "\n".join(getattr(ref.resolve(document), "text", "") for ref in item.footnotes)
                if isinstance(item, TableItem):
                    text = item.export_to_markdown(doc=document)
                    table_data = item.data.model_dump(mode="json")
                else:
                    text = caption
                image = item.get_image(document)
                if image is not None:
                    image_path = f"element-{index}.png"
                    image.save(directory / image_path)
            locations = [Provenance(page=prov.page_no,
                bbox=(prov.bbox.l, prov.bbox.t, prov.bbox.r, prov.bbox.b),
                coordinate_origin=str(prov.bbox.coord_origin.value)) for prov in item.prov]
            if not locations:
                continue
            elements.append(DocumentElement(element_id=str(item.self_ref), label=label,
                text=re.sub(r"[ \t]{2,}", " ", text) if label != "table" else text,
                provenance=locations, heading_level=getattr(item, "level", None),
                caption=caption, notes=notes, table_data=table_data, image=image_path))
        key = self.processing_key(material.source.sha256)
        source = material.source.model_copy(update={"artifact_key": material.source.sha256 + "/" + key})
        markdown = document.export_to_markdown()
        # Preserve text recovered by Docling inside figures, even when layout
        # assembly keeps those labels inside a PictureItem rather than prose.
        ocr_text = {page.page_no: "\n".join(cell.text for cell in page.cells
                    if getattr(cell, "from_ocr", False) and cell.text.strip())
                    for page in result.pages} if self.settings.do_ocr else {}
        document.save_as_json(directory / "docling.json", image_mode=ImageRefMode.REFERENCED)
        (directory / "document.md").write_text(markdown)
        return ExtractedDocument(source=source, parser_version=self.parser_version, processing_key=key,
            markdown=markdown, page_count=len(pages), elements=elements, page_images=pages,
            ocr_text=ocr_text)
