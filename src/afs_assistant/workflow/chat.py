"""Chat entry point: direct figure links, offline demo or the shared investigator."""
import re
from uuid import uuid4
from typing import Literal

from afs_assistant.core.models import AnswerPart, ChatAnswer, SearchRequest


class AnswerValidationError(ValueError):
    pass


class ChatResponder:
    def __init__(self, retrieval, settings, gateway=None, *, thread_id=None, progress=None):
        self.retrieval = retrieval
        self.settings = settings
        self.gateway = gateway
        self.trace = []
        self.thread_id = thread_id or uuid4().hex
        self.progress = progress

    @staticmethod
    def plain(text, kind: Literal["answer", "proposal", "question", "gap"] = "gap"):
        return ChatAnswer(parts=[AnswerPart(kind=kind, text=text)]), {}

    def answer(self, history: list[dict]) -> tuple[ChatAnswer, dict]:
        question = history[-1]["content"]
        figure = re.search(r"\b(?:figure|fig\.?)\s*(\d+)\b", question, re.IGNORECASE)
        reference = re.search(r"\b(?:AN|H|A|S)\s*\d+\b", question, re.IGNORECASE)
        matches = self.retrieval.figures(reference[0].replace(" ", ""), int(figure[1])) if figure and reference else []
        display_only = re.search(r"\b(?:show|open|link|display|see)\b", question, re.IGNORECASE) and not re.search(
            r"\b(?:what|why|how|explain|describe|compare|interpret|analy[sz]e|assess)\b", question, re.IGNORECASE)
        if figure and reference and display_only:
            self.trace.append({"tool": "lookup_figure", "evidence_ids": [item.evidence_id for item in matches]})
            if len(matches) != 1:
                return self.plain("I couldn’t locate an unambiguous figure with that document name and number.")
            item = matches[0]
            answer = ChatAnswer(parts=[AnswerPart(kind="answer", text=f"Here is Figure {int(figure[1])} from {item.title}. Open the citation to view it.", evidence_ids=[item.evidence_id])])
            return answer, {item.evidence_id: self.retrieval.context(item.evidence_id)}
        if self.gateway is None:
            found = self.retrieval.search(SearchRequest(query=question[:2000], limit=6))
            synthetic = [hit["evidence"] for hit in found["items"] if hit["evidence"]["synthetic"]]
            if synthetic:
                item = synthetic[0]
                return ChatAnswer(parts=[AnswerPart(kind="gap", text="This is an offline demonstration using fictional evidence."),
                    AnswerPart(kind="answer", text=item["text"], evidence_ids=[item["evidence_id"]])]), {item["evidence_id"]: self.retrieval.context(item["evidence_id"])}
            return self.plain("Model mode is needed to answer questions using the indexed documents.")

        from .agent import AgentResponder
        responder = AgentResponder(self.retrieval, self.settings, self.gateway,
            thread_id=self.thread_id, progress=self.progress)
        try:
            return responder.answer(history)
        finally:
            self.trace = responder.trace
