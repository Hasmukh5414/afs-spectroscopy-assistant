"""Read-only, release-scoped Python capabilities for the investigation agent."""
import json
import re
from pathlib import Path
import time
from typing import Literal

from langchain.tools import tool

from afs_assistant.core.models import SearchRequest
from afs_assistant.core.source_policy import visual_search_text
from .context import clipped


class InvestigationTools:
    def __init__(self, retrieval, settings, gateway, trace, deadline, progress=None, model_budget=None):
        self.retrieval, self.settings, self.gateway = retrieval, settings, gateway
        self.trace, self.deadline, self.progress = trace, deadline, progress
        self.aliases = {}
        self.inspected = {}
        self.calls = 0
        self.repeats = {}
        self.model_budget = model_budget
        self.time_reserve = 30
        # Extra evidence bytes only the method background step may use (see add_method_background):
        # the agent's own reads can fill the budget, and background still needs room.
        self.background_headroom = 8000
        self.budget_headroom = 0
        # Set on the agent's last turn: no later turn remains to read search results.
        self.read_on_search = 0
        # Evidence IDs the answer budget never drops (each proposed method's nominal range).
        self.protected = set()

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic() - self.time_reserve)

    def event(self, event):
        self.trace.append(event)
        if self.progress:
            self.progress(self.trace)

    def guard(self, name, args):
        if self.calls >= self.settings.max_tool_calls or self.remaining() <= 0:
            return {"error": "Investigation budget reached. Finish with supported findings and remaining gaps."}
        self.calls += 1
        key = json.dumps([name, args], sort_keys=True)
        self.repeats[key] = self.repeats.get(key, 0) + 1
        # Arguments show what the agent asked for when a source is missed later.
        self.event({"stage": "tool", "tool": name, "arguments": args, "status": "started", "call": self.calls})
        if self.repeats[key] > 2:
            return {"error": "This call already returned its evidence. Use a different query or finish."}
        return None

    # S01 describes each family in its own section; UV/Visible has none there.
    METHOD_SECTIONS = {"IR spectroscopy": r"^5\.1\.", "NIR spectroscopy": r"^5\.2\.",
                       "Raman spectroscopy": r"^5\.3\.", "Fluorimetry": r"^5\.4\."}
    METHOD_TERMS = {"IR spectroscopy": r"mid-? ?IR|mid-infrared", "NIR spectroscopy": r"\bNIR\b|near-infrared",
                    "Raman spectroscopy": r"raman", "Fluorimetry": r"fluori|fluoresc", "UV/Visible spectroscopy": r"\bUV\b|UV-visible"}

    # Shortlist names the answer step may choose, mapped to the workbook family and table query.
    BACKGROUND_FAMILIES = {"NIR": ("NIR spectroscopy", "nir"), "MIR/ATR": ("IR spectroscopy", "ir"),
                           "Raman": ("Raman spectroscopy", "raman"), "Fluorescence": ("Fluorimetry", "fluorimetry"),
                           "UV-Vis": ("UV/Visible spectroscopy", "uv/visible")}

    @staticmethod
    def s01_text(item, heading):
        return (item.source_id == "S01" and item.element_type == "text" and len(item.text) >= 150
                and item.heading_path and re.search(heading, item.heading_path[-1]))

    def method_section(self, family):
        """The family's own S01 method-section passages (none for UV/Visible)."""
        section = self.METHOD_SECTIONS.get(family)
        return [i for i in self.retrieval.items if section and self.s01_text(i, section)][:3]

    def capability_paragraphs(self, family):
        """S01 capability paragraphs naming the family, focused ones first."""
        terms = self.METHOD_TERMS.get(family)
        # Capability paragraphs focused on this family rank above ones that merely list every method.
        def focus(item):
            own_count = len(re.findall(terms or "", item.text, re.IGNORECASE))
            total = sum(len(re.findall(t, item.text, re.IGNORECASE)) for t in self.METHOD_TERMS.values())
            return (-own_count / max(total, 1), -own_count)
        return sorted((i for i in self.retrieval.items if terms and self.s01_text(i, r"^3\.1\.")
                       and re.search(terms, i.text, re.IGNORECASE)), key=focus)[:2]

    def table_rows(self, query):
        """Workbook rows whose method or technique matches the query as a whole word."""
        return [item for item in self.retrieval.items if item.worksheet
            and (not query or re.search(r"(?<!\w)" + re.escape(query) + r"(?!\w)", (
                str(item.structured_data.get("method", "")) + " " +
                str(item.structured_data.get("measurement_technique", ""))).casefold()))]

    def interface_rows(self, query):
        """Up to two process-interface rows for a method, probes first; laboratory cuvettes excluded."""
        rows = [row for row in self.table_rows(query)
                if "cuvette" not in str(row.structured_data.get("measurement_technique", "")).casefold()]
        rows.sort(key=lambda row: "probe" not in str(row.structured_data.get("measurement_technique", "")).casefold())
        return rows[:2]

    def add_method_background(self, methods):
        """Read each proposed method's own S01 section, then overview paragraphs, then interface rows.

        Tiers are filled across all methods in turn, so if the evidence budget runs out
        every method has kept its own section first. Each method's opening section
        passage (nominal range and limits) is protected from the answer budget.
        Returns the references added.
        """
        families = [self.BACKGROUND_FAMILIES[m] for m in dict.fromkeys(methods) if m in self.BACKGROUND_FAMILIES]
        sections = [self.method_section(family) for family, _ in families]
        self.protected |= {passages[0].evidence_id for passages in sections if passages}
        tiers = [[item for passages in sections for item in passages],
                 [item for family, _ in families for item in self.capability_paragraphs(family)],
                 [item for _, query in families for item in self.interface_rows(query)]]
        added = []
        self.budget_headroom = self.background_headroom
        try:
            for tier in tiers:
                for item in tier:
                    if any(record.get("evidence_id") == item.evidence_id for record in self.inspected.values()):
                        continue
                    try:
                        record = self.expose(item.evidence_id, inspected=True)
                    except ValueError:
                        return added  # Evidence budget reached; keep what fits.
                    if record["inspected"]:
                        added.append(record["reference"])
            return added
        finally:
            self.budget_headroom = 0

    # Inspected passages the answer step receives, besides protected method sections.
    ANSWER_EVIDENCE = 12

    def focus(self, question):
        """Keep the inspected evidence closest to the customer's question for the answer step.

        Everything read is ranked by its stored embedding's similarity to the full
        question (one query embedding, no extra service). The best ANSWER_EVIDENCE are
        kept, plus protected method sections; the rest is dropped so it cannot be cited.
        Without embeddings nothing changes. Returns the dropped references.
        """
        refs = list(self.inspected)
        if len(refs) <= self.ANSWER_EVIDENCE:
            return []
        items = [self.retrieval.by_id[self.inspected[ref]["evidence_id"]] for ref in refs]
        scores = self.retrieval.similarity(question[:2000], items, self.remaining())
        if scores is None:
            return []
        keep = {ref for ref, item in zip(refs, items) if item.evidence_id in self.protected}
        ranked = [ref for ref, _ in sorted(zip(refs, scores), key=lambda pair: pair[1], reverse=True) if ref not in keep]
        keep |= set(ranked[:self.ANSWER_EVIDENCE])
        dropped = [ref for ref in refs if ref not in keep]
        for ref in dropped:
            del self.inspected[ref]
        return dropped

    # Read in code for the customer's whole wording, before the agent's gap turn.
    QUESTION_PASSAGES, QUESTION_ROWS = 4, 2
    def read_question_evidence(self, question):
        """Search the customer's full wording and read its best passages without a model call.

        Whole-question retrieval ranks the application pages that short agent queries
        miss. Passages and comparison rows are ranked separately so rows cannot crowd
        out passages, and one passage per page is read so a slide and its image do
        not fill two slots. Lower-ranked hits stay as unread previews. Returns (read, previews).
        """
        read, unread, pages, modes = [], [], set(), []
        for types, limit, shown in ((["text", "figure_caption", "image"], self.QUESTION_PASSAGES, 8),
                                    (["table_row"], self.QUESTION_ROWS, self.QUESTION_ROWS)):
            result = self.retrieval.search(SearchRequest.model_validate({"query": question[:2000],
                "element_types": types, "limit": shown}), self.remaining())
            modes.append(result["mode"])
            items = [self.retrieval.by_id[hit["evidence"]["evidence_id"]] for hit in result["items"]]
            # A page's text is read over its image; the figure stays attached for display.
            text_pages = {(item.source_id, item.locator.label) for item in items if not item.image}
            kept = 0
            for item in items:
                eid, page = item.evidence_id, (item.source_id, item.locator.label)
                record = None
                if kept < limit and page not in pages and not (item.image and page in text_pages):
                    try:
                        record = self.expose(eid, inspected=True)
                    except ValueError:
                        pass  # Evidence budget reached; the rest stay previews.
                if record and record["inspected"]:
                    read.append(record)
                    pages.add(page)
                    kept += 1
                else:
                    unread.append(eid)
        # Previews are numbered after the read passages, so E1.. are what was read.
        previews = [self.expose(eid) for eid in dict.fromkeys(unread)]
        self.event({"stage": "question_evidence", "references": [r["reference"] for r in read],
            "previews": [r["reference"] for r in previews], "modes": modes})
        return read, previews

    def unread_passages(self):
        """Returned non-table candidates that were discovered but never read."""
        return [ref for ref, eid in self.aliases.items() if ref not in self.inspected
                and eid in self.retrieval.by_id and not self.retrieval.by_id[eid].worksheet]

    def has_read_passage(self):
        # Table lookups mark comparison rows inspected; they are not method or application text.
        return any(not record.get("table_row") for record in self.inspected.values())

    def alias(self, eid):
        if eid not in self.aliases.values():
            self.aliases[f"E{len(self.aliases)+1}"] = eid
        return next(ref for ref, value in self.aliases.items() if value == eid)

    def resolve(self, reference):
        eid = self.aliases.get(reference)
        if eid is None and reference in self.aliases.values():
            eid = reference  # Returned candidate IDs are as explicit as E aliases.
        if eid is None or eid not in self.retrieval.by_id:
            raise ValueError("Use an evidence reference returned by a knowledge tool.")
        item = self.retrieval.by_id[eid]
        if item.review_status == "disputed":
            raise ValueError("Disputed evidence is unavailable.")
        return eid

    def expose(self, eid, *, inspected=False, context=False):
        item = self.retrieval.by_id[eid]
        source = self.retrieval.source_by_id.get(item.source_id)
        record = {"reference": self.alias(eid), "evidence_id": eid, "source_id": item.source_id,
            "title": item.title, "location": item.locator.label, "kind": item.element_type,
            "headings": item.heading_path,
            "role": source.evidence_role if source else "technical",
            "authority": source.authority if source else "supplementary",
            "inspected": inspected, "text": clipped(item.text, 2400 if inspected else 600)[0]}
        record["cite_as"] = item.citation_label or f"{item.source_id} {item.locator.label}"
        record["truncated"] = len(item.text) > len(record["text"])
        curated = any(element.startswith("curated-") for element in item.element_ids)
        if curated:
            record["scope"] = ("Manual transcription of supplied image-only content, citable as this page. "
                "It adds no external knowledge; bracketed values are unit conversions.")
        if curated or any(element.endswith("-ocr") for element in item.element_ids):
            if not curated:
                record["layout_warning"] = (
                    "Flattened OCR is discovery text. Reading order does not establish chart labels, "
                    "range endpoints, units or row associations. Inspect the related image with a "
                    "specific visual question before reporting such values; otherwise leave them unknown.")
            pages = {p.page for p in item.provenance}
            record["related_images"] = [self.alias(candidate.evidence_id)
                for candidate in self.retrieval.items if candidate.source_id == item.source_id
                and candidate.image and any(p.page in pages for p in candidate.provenance)]
        if item.image:
            record["text"] = clipped(item.image.caption + "\n" + item.image.nearby_text, 1800 if inspected else 600)[0]
            record["original_caption"] = item.image.caption
            record["visual_description"] = visual_search_text(item.image.description.text)[0][:1800] if item.image.description else ""
            record["visual_description_status"] = "AI-generated interpretation; inspect original pixels for exact details"
        if item.structured_data:
            # The row is the evidence; its text duplicates it and the workbook note is
            # identical for every row, so lookups return that note once.
            record.pop("text", None)
            record["table_row"] = item.structured_data
            record["scope"] = ("Qualitative ratings, not measured performance; optical path/depth is sampling "
                "geometry, not spectral coverage. Keep method, geometry and sample type together.")
        if source and source.evidence_role == "business":
            record["scope"] = "Company/process context, not experimental validation or a hardware specification."
        if item.element_ids and all(element.endswith("-heading") for element in item.element_ids):
            record["inspected"] = False
            record["scope"] = "Heading only: discovery metadata, not factual supporting evidence. Read substantive text or inspect the corresponding figure."
            inspected = False
        if inspected:
            tentative = {**self.inspected, record["reference"]: record}
            if len(json.dumps(tentative, ensure_ascii=False).encode()) > self.settings.evidence_byte_budget + self.budget_headroom:
                raise ValueError("Active evidence budget reached. Finish with already inspected evidence.")
            self.inspected[record["reference"]] = record
            self.event({"stage": "evidence", "tool": "read_evidence", "evidence_ids": [eid],
                "source_id": item.source_id, "title": item.title, "location": item.locator.label,
                "reference": record["reference"], "status": "completed"})
        if context and inspected and item.parent_context_id and record["inspected"]:
            # Keep adjacent text in separate observations with its own locator and
            # reference. Never make an opening chunk authorize a whole section.
            siblings = [candidate for candidate in self.retrieval.items
                if candidate.source_id == item.source_id
                and candidate.parent_context_id == item.parent_context_id
                and candidate.element_type == "text"]
            ids = [candidate.evidence_id for candidate in siblings]
            if eid in ids:
                index = ids.index(eid)
                record = dict(record)
                record["adjacent_evidence"] = []
                for neighbor in siblings[max(0, index-1):index+3]:
                    if neighbor.evidence_id == eid:
                        continue
                    try:
                        record["adjacent_evidence"].append(self.expose(neighbor.evidence_id, inspected=True))
                    except ValueError:
                        record["context_truncated"] = True
                        break
        return record

    def restore(self, messages):
        """Restore evidence aliases from checkpointed tool observations, never model claims."""
        def visit(value):
            if isinstance(value, dict):
                ref, eid = value.get("reference"), value.get("evidence_id")
                if isinstance(ref, str) and eid in self.retrieval.by_id:
                    self.aliases[ref] = eid
                    if value.get("inspected"):
                        self.inspected[ref] = {key: child for key, child in value.items()
                            if key != "adjacent_evidence"}
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        for message in messages:
            if getattr(message, "type", "") == "tool":
                try:
                    visit(json.loads(message.content))
                except (ValueError, TypeError):
                    pass

    def tools(self):
        @tool
        def search_knowledge(query: str, source_ids: list[str] | None = None,
                             element_types: list[Literal["text", "table_row", "figure_caption", "image"]] | None = None,
                             roles: list[Literal["technical", "research", "business"]] | None = None):
            """Find candidate AFS text, table or image evidence. Scores do not prove relevance. Read relevant candidates before citing. On the last investigation turn the top passages are read automatically."""
            if error := self.guard("search_knowledge", {"query": query, "sources": source_ids, "types": element_types, "roles": roles}):
                return error
            try:
                request = SearchRequest.model_validate({"query": query, "source_ids": source_ids or [],
                    "element_types": element_types or [], "roles": roles or [], "limit": 5})
            except ValueError:
                return {"error": "Use valid source IDs, roles technical/research/business and element types text/table_row/figure_caption/image."}
            result = self.retrieval.search(request, self.remaining())
            records = []
            read = 0
            for hit in result["items"]:
                eid = hit["evidence"]["evidence_id"]
                record = None
                if read < self.read_on_search and not self.retrieval.by_id[eid].worksheet:
                    try:
                        record = self.expose(eid, inspected=True)
                        read += record["inspected"]
                    except ValueError:
                        pass  # Evidence budget reached; return a preview.
                record = record or self.expose(eid)
                record.update(cosine_similarity=hit.get("cosine_similarity"), bm25_score=hit.get("bm25_score"),
                    fused_score=hit["score"], routes=hit["routes"])
                records.append(record)
            self.event({"stage": "search", "tool": "search_knowledge", "query": query,
                "candidate_count": len(records), "mode": result["mode"], "status": "completed"})
            return {"candidates": records, "gaps": result["gaps"]}

        @tool
        def read_evidence(reference: str = "", include_context: bool = False,
                          references: list[str] | None = None):
            """Read one returned candidate (E alias or evidence_id), or up to six together. Returns only requested chunks; include_context explicitly adds bounded adjacent passages with separate citations. No automatic method definitions. Batch independent reads to avoid model round trips."""
            if error := self.guard("read_evidence", {"reference": reference, "references": references, "context": include_context}):
                return error
            if references is not None and reference:
                return {"error": "Supply reference for one passage or references for a batch, not both."}
            requested = references if references is not None else [reference]
            if not requested or len(requested) > 6 or any(not ref for ref in requested):
                return {"error": "Supply between one and six evidence references returned by a knowledge tool."}
            records, errors = [], []
            for ref in dict.fromkeys(requested):
                try:
                    records.append(self.expose(self.resolve(ref), inspected=True, context=include_context))
                except ValueError as exc:
                    errors.append({"reference": ref, "error": str(exc)})
            if references is None:
                return records[0] if records else {"error": errors[0]["error"]}
            return {"passages": records, "errors": errors}

        @tool
        def lookup_method_table(method: str = "", methods: list[str] | None = None):
            """Read supplied comparison rows for one or more families such as NIR, Raman, MIR, fluorescence or UV-Vis (use methods for several in one call). Each proposed method's own S01 description is read automatically before the answer. Optical depth is NOT wavelength; ratings are qualitative. Application evidence still needs search_knowledge."""
            requested = [m for m in (methods or []) + ([method] if method else []) if m.strip()] or [""]
            if error := self.guard("lookup_method_table", {"methods": requested}):
                return error
            rows, seen = [], set()
            for name in requested:
                query = name.casefold().strip()
                query = {"mir": "ir", "ftir": "ir", "mir atr": "ir", "atr": "ir", "fluorescence": "fluorimetry",
                    "uv-vis": "uv/visible", "uv/vis": "uv/visible", "uv": "uv/visible"}.get(query, query)
                matches = self.table_rows(query)
                for item in matches[:6]:
                    if item.evidence_id not in seen:
                        seen.add(item.evidence_id)
                        rows.append(item)
            result = {"rows": [], "table_notes": rows[0].qualifications if rows else [],
                "scope": "Table rows describe qualitative comparison and optical depth, not spectral coverage."}
            try:
                for item in rows:
                    result["rows"].append(self.expose(item.evidence_id, inspected=True))
            except ValueError as exc:
                result["gap"] = str(exc)
            return result

        @tool
        def get_source_outline(source_id: str = ""):
            """List supplied sources or inspect headings, pages, slides and image references in a selected source."""
            if error := self.guard("get_source_outline", {"source_id": source_id}):
                return error
            if not source_id:
                return {"sources": [{"source_id": s.source_id, "title": s.title, "role": s.evidence_role,
                    "format": s.format} for s in self.retrieval.corpus.sources]}
            source = self.retrieval.source_by_id.get(source_id)
            if source is None:
                return {"error": "Unknown source ID."}
            entries = []
            seen = set()
            for item in self.retrieval.items:
                if item.source_id != source_id:
                    continue
                key = (item.locator.label, tuple(item.heading_path))
                if key not in seen or item.image:
                    entries.append({"location": item.locator.label, "headings": item.heading_path,
                        "kind": item.element_type, "reference": self.alias(item.evidence_id)})
                    seen.add(key)
            return {"source_id": source_id, "entries": entries[:50], "truncated": len(entries) > 50}

        @tool
        def inspect_figure(reference: str, visual_question: str = ""):
            """Inspect a candidate image and its original caption. Supply a specific visual question to examine the actual pixels with the vision service."""
            if error := self.guard("inspect_figure", {"reference": reference, "question": visual_question}):
                return error
            try:
                eid = self.resolve(reference)
                item = self.retrieval.by_id[eid]
                if not item.image:
                    return {"error": "That evidence is not an image."}
                record = self.expose(eid, inspected=True, context=True)
                if visual_question:
                    if not self.gateway or not self.settings.vision_model:
                        return {"evidence": record, "gap": "Live visual inspection is unavailable."}
                    source = self.retrieval.source_by_id[item.source_id]
                    root = (self.settings.data_dir / "processed").resolve()
                    path = (root / source.artifact_key / item.image.filename).resolve()
                    if not path.is_relative_to(root) or not path.is_file():
                        return {"evidence": record, "gap": "The retained image is unavailable."}
                    prompt = (Path(__file__).parents[1] / "indexing/prompts/describe_image.md").read_text(encoding="utf-8")
                    if self.model_budget and not self.model_budget():
                        return {"evidence": record, "gap": "Model-call budget is reserved for the final answer."}
                    observation = self.gateway.describe_image(path.read_bytes(),
                        f"Specific question: {visual_question}\nOriginal caption: {item.image.caption}", prompt,
                        reasoning_effort="none", timeout=self.remaining())
                    record = {**record, "visual_observation": visual_search_text(observation)[0]}
                    tentative = {**self.inspected, record["reference"]: record}
                    if len(json.dumps(tentative, ensure_ascii=False).encode()) > self.settings.evidence_byte_budget:
                        return {"evidence": self.inspected[record["reference"]],
                            "gap": "The extra visual observation exceeds the active evidence budget."}
                    self.inspected[record["reference"]] = record
                return record
            except ValueError as exc:
                return {"error": str(exc)}

        @tool
        def lookup_references(source_id: str, label_or_title: str = ""):
            """Look up bibliography metadata in one source. An absent paper's contents are unavailable and cannot support scientific claims."""
            if error := self.guard("lookup_references", {"source_id": source_id, "query": label_or_title}):
                return error
            query = label_or_title.casefold().strip()
            refs = [r for r in self.retrieval.corpus.references if r.source_id == source_id
                and (not query or query == r.label.casefold() or query in r.citation.casefold())]
            return {"references": [r.model_dump() for r in refs[:10]], "more_references": len(refs) > 10,
                "scope": "Metadata only. Retrieve a target supplied document before relying on its findings."}

        return [search_knowledge, read_evidence, lookup_method_table, inspect_figure,
                get_source_outline, lookup_references]
