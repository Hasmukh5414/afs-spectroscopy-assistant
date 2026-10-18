from typing import Literal
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Locator(Contract):
    kind: Literal["page", "slide", "row", "section"]
    label: str = Field(min_length=1, max_length=200)


class Provenance(Contract):
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None = None
    coordinate_origin: str = "TOPLEFT"


class SourceVersion(Contract):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=300)
    uri: str
    filename: str
    kind: Literal["application", "method_comparison", "specification", "guidance"]
    expected_pages: int = Field(ge=1, le=200)
    issue_ids: list[str] = Field(default_factory=list)
    access_scope: Literal["public"] = "public"
    redistribution: str = "unverified"
    artifact_key: str = ""
    format: Literal["pdf", "xlsx", "html"] = "pdf"
    authority: Literal["provided", "supplementary"] = "supplementary"
    evidence_role: Literal["technical", "research", "business"] = "technical"
    visual_strategy: Literal["regions", "page"] = "regions"
    retained_path: str = ""
    citation_keys: list[str] = Field(default_factory=list)
    structured_table_pages: list[int] = Field(default_factory=list)
    snapshot_at: str = ""


class SourceMaterial(Contract):
    source: SourceVersion
    path: Path


class DocumentElement(Contract):
    element_id: str
    label: str
    text: str
    provenance: list[Provenance] = Field(default_factory=list)
    heading_level: int | None = None
    caption: str = ""
    notes: str = ""
    table_data: dict = Field(default_factory=dict)
    image: str | None = None


class ReferenceRecord(Contract):
    source_id: str
    label: str
    citation: str
    location: str
    doi: str = ""
    url: str = ""
    target_source_id: str | None = None
    status: Literal["metadata_only", "supplied_document", "unresolved_marker"] = "metadata_only"


class ExtractedDocument(Contract):
    source: SourceVersion
    parser_version: str
    processing_key: str
    markdown: str
    page_count: int
    elements: list[DocumentElement]
    page_images: dict[int, str] = Field(default_factory=dict)
    references: list[ReferenceRecord] = Field(default_factory=list)
    coverage: list[dict] = Field(default_factory=list)
    ocr_text: dict[int, str] = Field(default_factory=dict)


class EvidenceContext(Contract):
    context_id: str
    text: str = Field(max_length=100000)
    provenance: list[Provenance] = Field(default_factory=list)


class ImageDescription(Contract):
    text: str = Field(min_length=30, max_length=6000)
    model: str
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cache_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    generated_at: str
    reasoning_effort: str = ""
    fallback_from_reasoning: str | None = None
    review_status: Literal["unreviewed"] = "unreviewed"


class EvidenceImage(Contract):
    filename: str = Field(pattern=r"^element-[0-9]+\.png$")
    caption: str = ""
    nearby_text: str = Field(default="", max_length=1200)
    interpretation: Literal["caption_and_context_only", "vision_description_unreviewed"] = "caption_and_context_only"
    description: ImageDescription | None = None
    review_notes: list[str] = Field(default_factory=list, max_length=20)
    classification: Literal["unclassified"] = "unclassified"


class EvidenceItem(Contract):
    evidence_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    source_id: str = Field(min_length=1, max_length=100)
    source_version: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=20000)
    locator: Locator
    kind: Literal["application", "method_comparison", "specification", "guidance"]
    review_status: Literal["extracted", "reviewed", "disputed"] = "extracted"
    qualifications: list[str] = Field(default_factory=list, max_length=20)
    synthetic: bool = True
    element_ids: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    parent_context_id: str | None = None
    element_type: Literal["text", "table_row", "figure_caption", "image"] = "text"
    image: EvidenceImage | None = None
    issue_ids: list[str] = Field(default_factory=list)
    oversized: bool = False
    worksheet: str = ""
    cell_range: str = ""
    structured_data: dict = Field(default_factory=dict)
    citation_label: str = ""
    related_source_id: str = ""
    related_page: int | None = None


class Corpus(Contract):
    release: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    items: list[EvidenceItem] = Field(max_length=5000)
    sources: list[SourceVersion] = Field(default_factory=list, max_length=100)
    contexts: list[EvidenceContext] = Field(default_factory=list, max_length=5000)
    processing: dict = Field(default_factory=dict)
    references: list[ReferenceRecord] = Field(default_factory=list)


class ChunkBatch(Contract):
    items: list[EvidenceItem]
    contexts: list[EvidenceContext]


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=1)


class SearchRequest(Contract):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=10)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    element_types: list[Literal["text", "table_row", "figure_caption", "image"]] = Field(default_factory=list)
    roles: list[Literal["technical", "research", "business"]] = Field(default_factory=list)


class Question(Contract):
    field: Literal["target", "matrix", "conditions", "requirements"]
    question: str = Field(min_length=1, max_length=500)
    affects: str = Field(min_length=1, max_length=500,
        description="The concrete choice, validation or installation decision this missing answer could change.")


class Claim(Contract):
    text: str = Field(min_length=1, max_length=2000,
        description="One factual finding supported by the cited passages at their actual scope; preserve conditions and uncertainty. Do not bundle unrelated claims or treat a source example as this customer's demonstrated result.")
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class Candidate(Contract):
    name: str = Field(min_length=1, max_length=300,
        description="A conditionally justified option to assess, not a predetermined winner or mandatory replacement.")
    claims: list[Claim] = Field(min_length=1, max_length=10)
    assumptions: list[str] = Field(max_length=10,
        description="Conditions specific to this option that must hold for it to work. Not missing customer facts (those are questions/unknowns) and not risks shared by all options (those are gaps).")
    limitations: list[str] = Field(min_length=1, max_length=10,
        description="Weaknesses that distinguish this option from the others. A risk that applies to every option belongs once in gaps, not repeated per option.")
    evidence_ids: list[str] = Field(default_factory=list, max_length=10,
        description="Inspected references supporting this candidate's measurement concept and spectral range.")
    measurement_concept: str = Field(default="", max_length=1500,
        description="High-level geometry and sampling concept. Label source hardware/settings as examples; keep installation conditional on unconfirmed process compatibility.")
    spectral_range: str = Field(default="", max_length=1000)


class Requirement(Contract):
    statement: str = Field(max_length=1000,
        description="Only the quoted customer's fact/goal; preserve qualifiers, target-to-matrix associations and uncertainty.")
    user_quote: str = Field(min_length=1, max_length=1500)


class Experiment(Contract):
    objective: str = Field(max_length=1000,
        description="The customer decision to resolve, including applicability of an existing approach when relevant; do not predetermine failure, replacement or superiority.")
    steps: list[str] = Field(max_length=10,
        description="Practical representative sampling, paired references and fair comparison; separate fitting from held-out validation where applicable. Start with information gathering when prerequisites are unknown.")
    criteria_to_confirm: list[str] = Field(max_length=10,
        description="Neutral decisions against customer requirements, including adequate simpler options, no useful gain or inconclusive results. Statistical significance alone does not justify complexity. No invented numerical targets.")


class Assessment(Contract):
    status: Literal["ready_for_review", "awaiting_clarification", "incomplete"]
    summary: str = Field(min_length=1, max_length=2000)
    candidates: list[Candidate] = Field(default_factory=list, max_length=4)
    questions: list[Question] = Field(default_factory=list, max_length=4)
    experiment: Experiment | None = None
    gaps: list[str] = Field(default_factory=list, max_length=15,
        description="Risks and evidence limits that apply to the whole assessment, each stated once.")
    requirements: list[Requirement] = Field(default_factory=list, max_length=12)
    unknowns: list[str] = Field(default_factory=list, max_length=12,
        description="Missing customer facts, each stated once; ask about decisive ones in questions instead of repeating them elsewhere.")


class ChatQuestion(Contract):
    text: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    expected_revision: int = Field(ge=1)


class NewChat(Contract):
    text: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


class AnswerPart(Contract):
    kind: Literal["answer", "proposal", "question", "gap"] = Field(
        description="answer: sourced technical findings/interpretation with references; proposal: suggested actions/study, not established results; question: missing customer information; gap: evidence limits or unsupported requested certainty.")
    text: str = Field(min_length=1, max_length=1800,
        description="Answer only the current request. Distinguish sourced findings, conditional interpretation and proposed actions. Keep qualifications consistent with the structured assessment; one coherent set of claims per part.")
    # Sentence-level markers can give one paragraph several sources.
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class FieldCitation(Contract):
    field: str = Field(min_length=1, max_length=180,
        description="Exact prose field path, e.g. assessment.candidates[0].limitations[0].")
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class ChatAnswer(Contract):
    parts: list[AnswerPart] = Field(min_length=1, max_length=8)
    schema_version: int = 3
    citations: list[FieldCitation] = Field(default_factory=list, max_length=40,
        description="References for structured prose fields without their own evidence_ids. Never put E aliases in prose.")
    intent: Literal["qa", "assessment"] = "qa"
    assessment: Assessment | None = None
    citation_status: Literal["present", "present_with_gaps", "none"] | None = Field(
        default=None, description="Citation presence and stated gaps only; not scientific claim verification.")
    # Accept historical schema-v2 records, but never emit their misleading coverage label.
    evidence_coverage: Literal["supported", "partial", "none"] | None = Field(default=None, exclude=True)
