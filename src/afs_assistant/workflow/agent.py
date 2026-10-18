"""The production investigation engine shared by chat questions and customer assessments."""
import json
from pathlib import Path
import time
from typing import Literal, cast

from pydantic import Field
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import InputAgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, convert_to_messages
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from afs_assistant.core.gateway import CLAUDE_EFFORTS, CLAUDE_MIN_OUTPUT_TOKENS, TruncatedAnswerError
from afs_assistant.core.models import AnswerPart, Assessment, ChatAnswer, Contract
from afs_assistant.core.tool_schema import tool_parameters
from afs_assistant.core.citations import normalize_citations, number_markers, prose_fields, strip_markers
from afs_assistant.core.fingerprints import fingerprint
from .chat import AnswerValidationError, ChatResponder
from .query import plan_query
from .tools import InvestigationTools
from .verify import verify
from .omission import omit_failing_fields

# The question's own evidence is read in code; the agent gets one turn to fill a gap.
AGENT_TURNS = 1


class MethodShortlist(Contract):
    """Methods the assessment will propose; their source background is then read in code."""
    methods: list[Literal["NIR", "MIR/ATR", "Raman", "Fluorescence", "UV-Vis"]] = Field(default_factory=list, max_length=5)


def with_timeout(model, seconds):
    """Copy a chat model with a shorter per-request timeout."""
    if isinstance(model, ChatAnthropic):
        bounded = model.model_copy(update={"default_request_timeout": seconds})
        # ChatAnthropic caches a client built with the old timeout; rebuild it on first use.
        for cached in ("_client_params", "_client", "_async_client"):
            vars(bounded).pop(cached, None)
        return bounded
    return model.model_copy(update={"request_timeout": seconds})


class InvestigationBudget(AgentMiddleware):
    def __init__(self, owner):
        self.owner = owner

    def wrap_model_call(self, request, handler):
        owner = self.owner
        # Reserve answer generation and one bounded repair.
        if owner.model_calls >= owner.settings.max_model_calls - owner.reserved_calls or owner.tools.remaining() <= 0:
            return AIMessage(content="Investigation budget reached. Summarize inspected evidence and gaps.")
        if owner.tools.calls >= owner.settings.max_tool_calls or owner.agent_turns >= AGENT_TURNS:
            return AIMessage(content="Tool budget reached. Finish from inspected evidence.")
        owner.agent_turns += 1
        owner.model_calls += 1
        owner.tools.event({"stage": "investigating", "model_call": owner.model_calls})
        model = with_timeout(request.model, min(owner.settings.provider_timeout, owner.tools.remaining()))
        # Require inspected evidence, without prescribing the discovery tool.
        grounding_required = not owner.tools.inspected
        choice = "auto" if owner.settings.provider == "anthropic" or not grounding_required else "required"
        bounded = request.override(model=model, tool_choice=choice)
        owner.tools.read_on_search = 2 if owner.agent_turns == AGENT_TURNS else 0
        # Search results are previews. A last turn spent searching again would leave
        # only table rows citable, so the final turn reads unread passages instead.
        unread = owner.tools.unread_passages()
        if (owner.agent_turns == AGENT_TURNS and unread and not owner.tools.has_read_passage()
                and owner.settings.provider != "anthropic"):
            owner.tools.event({"stage": "final_turn_read_required", "unread": len(unread)})
            bounded = request.override(model=model, tool_choice="read_evidence", messages=[*request.messages, HumanMessage(
                content="This is the last investigation turn and no returned passage has been read. Read the most "
                "relevant unread candidates now with read_evidence (references, up to six); only read passages can be cited.")])
        response = handler(bounded)
        if (grounding_required and not any(getattr(m, "tool_calls", []) for m in response.result)
                and owner.model_calls < owner.settings.max_model_calls - owner.reserved_calls
                and owner.tools.remaining() > 0):
            # Some compatible providers accept required tool_choice but still
            # return prose. Retry that omission once, without injecting the
            # unsupported draft into the scientific evidence.
            owner.model_calls += 1
            owner.tools.event({"stage": "tool_call_retry", "model_call": owner.model_calls})
            retry_model = with_timeout(model, min(owner.settings.provider_timeout, owner.tools.remaining()))
            instruction = ("You have not inspected supporting evidence. Do not answer in prose. "
                "Choose an available knowledge tool and arguments that resolve the user's actual question.")
            response = handler(bounded.override(model=retry_model, messages=[*request.messages, HumanMessage(content=instruction)]))
        return response


class AgentResponder:
    def __init__(self, retrieval, settings, gateway, *, thread_id, progress=None, model=None):
        self.retrieval, self.settings, self.gateway = retrieval, settings, gateway
        self.thread_id, self.progress, self.model = thread_id, progress, model
        self.trace = []
        self.model_calls = 0
        self.agent_turns = 0
        self.reserved_calls = 2

    def chat_model(self):
        s = self.settings
        if s.provider == "anthropic":
            # Thinking is always on and counts toward max_tokens; effort is the depth control.
            return ChatAnthropic.model_validate({"model": s.model, "api_key": s.api_key, "max_retries": 0,
                "timeout": s.provider_timeout, "max_tokens": CLAUDE_MIN_OUTPUT_TOKENS,
                "reasoning_effort": s.reasoning_effort if s.reasoning_effort in CLAUDE_EFFORTS else None})
        return ChatOpenAI(model=s.model, base_url=s.api_base, api_key=s.api_key,
            temperature=0, max_retries=0, timeout=s.provider_timeout,
            extra_body={"max_tokens": 800},
            http_client=self.gateway._get_client(),
            reasoning_effort=s.reasoning_effort or None,
            model_kwargs={"parallel_tool_calls": False})

    def reserve_vision_call(self):
        if self.model_calls >= self.settings.max_model_calls - self.reserved_calls:
            return False
        self.model_calls += 1
        self.tools.event({"stage": "visual_inspection", "model_call": self.model_calls})
        return True

    def answer(self, history):
        started_at = time.monotonic()
        deadline = started_at + self.settings.run_timeout
        self.tools = InvestigationTools(self.retrieval, self.settings, self.gateway,
            self.trace, deadline, self.progress, self.reserve_vision_call)
        self.tools.event({"stage": "routing", "status": "started"})
        self.model_calls += 1
        try:
            plan = plan_query(history, self.gateway, min(self.settings.provider_timeout, deadline-time.monotonic()))
        except (ValueError, KeyError, TypeError) as exc:
            raise AnswerValidationError("The query router returned unusable structured data") from exc
        self.tools.time_reserve = min(100 if plan.intent == "assessment" else 30,
            self.settings.run_timeout * .4)
        # Assessments also reserve the method shortlist call.
        self.reserved_calls = 3 if plan.intent == "assessment" else 2
        routed_at = time.monotonic()
        self.tools.event({"stage": "query_plan", "route": plan.route, "intent": plan.intent})
        if plan.route == "out_of_scope":
            return ChatResponder.plain("That question is outside my scope as a spectroscopy and industrial measurement assistant.")
        if plan.route == "social":
            return ChatResponder.plain("Hi! What would you like to measure or understand about spectroscopy?", "answer")
        if plan.route == "clarify":
            return ChatResponder.plain("Which material, measurement or spectroscopy topic are you referring to?", "question")
        if not self.retrieval.corpus.sources or any(
                s.authority != "provided" for s in self.retrieval.corpus.sources):
            raise AnswerValidationError("The investigation requires the supplied official-material release.")
        model = self.model or self.chat_model()
        prompt_dir = Path(__file__).with_name("prompts")
        system = prompt_dir.joinpath("investigate.md").read_text(encoding="utf-8")
        user_text = "\n".join(m["content"] for m in history[-12:] if m["role"] == "user")
        checkpoint_path = self.settings.database.parent / "agent-checkpoints.sqlite3"
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        config: RunnableConfig = {"configurable": {"thread_id": self.thread_id + ":explicit-evidence-v1"},
            "recursion_limit": 40, "max_concurrency": 1}
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            agent = create_agent(model, tools=self.tools.tools(), system_prompt=system,
                middleware=[InvestigationBudget(self)], checkpointer=saver)
            previous = agent.get_state(config)
            messages = previous.values.get("messages", []) if previous.values else []
            self.tools.restore(messages)
            investigated = bool(self.tools.inspected)
            if messages and not previous.next and investigated:
                result = previous.values
            else:
                inputs = None
                if not previous.next:
                    if messages:
                        self.tools.aliases.clear()
                        self.tools.inspected.clear()
                    inputs = cast(InputAgentState, {"messages": [*convert_to_messages([*history[-12:], {
                        "role": "user", "content": json.dumps({
                            "resolved_request": plan.search_query, "intent": plan.intent,
                            "release": self.retrieval.corpus.release}, ensure_ascii=False)}]),
                        *self.question_evidence(user_text)]})
                    if messages:
                        inputs["messages"].insert(0, {"type": "remove", "id": REMOVE_ALL_MESSAGES, "content": ""})
                result = agent.invoke(inputs, config)
            # Restoring observations also covers a tool that finished just before
            # an interruption, without trusting citations in an assistant draft.
            self.tools.restore(result["messages"])
        if not self.tools.inspected:
            if self.tools.calls == 0:
                raise AnswerValidationError("The model did not call a knowledge tool; no grounded investigation was completed.")
            topic = plan.search_query.strip().rstrip(" .?!") or "this request"
            message = (f"I couldn't establish supporting evidence for: {topic}. "
                "I can't give a grounded answer from this investigation. A further source check is needed.")
            if plan.intent == "assessment":
                return ChatAnswer(intent="assessment", parts=[AnswerPart(kind="gap", text=message)],
                    assessment=Assessment(status="incomplete", summary=message,
                        gaps=["No relevant supporting source was inspected."]), citation_status="none"), {}
            answer, sources = ChatResponder.plain(message)
            answer.citation_status = "none"
            return answer, sources
        if plan.intent == "assessment":
            self.add_method_background(history, deadline)
        if dropped := self.tools.focus(user_text):
            self.tools.event({"stage": "evidence_focused", "kept": list(self.tools.inspected), "dropped": dropped})
        formatting_at = time.monotonic()
        self.tools.event({"stage": "formatting", "status": "started"})
        messages, schema = self.generation_request(history, plan)
        formatter_prompt = messages[0]["content"]

        def generate_checked(active_messages):
            # Structure, quotes, citations and quantities share one repair budget.
            repairs_remaining = 1
            while True:
                if self.model_calls >= self.settings.max_model_calls or time.monotonic() >= deadline:
                    raise TimeoutError("Answer generation budget exhausted")
                self.model_calls += 1
                raw = ""
                try:
                    # The written answer may use its own effort: on Claude, reasoning shares the
                    # output limit, and long evidence-rich answers otherwise get cut off.
                    raw = self.gateway.answer_json(active_messages, schema,
                        min(self.settings.provider_timeout, deadline-time.monotonic()), max_tokens=5000,
                        reasoning_effort=self.settings.answer_reasoning_effort or None)
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("Answer must be a JSON object.")
                    payload["intent"] = plan.intent
                    answer = ChatAnswer.model_validate(payload)
                    answer.parts = [p for p in answer.parts if not (
                        p.kind == "proposal" and plan.intent == "qa" and not plan.wants_experiment)]
                    if not answer.parts:
                        raise ValueError("Answer the user's question directly.")
                    answer = normalize_citations(answer)
                    for part in answer.parts:
                        if part.kind == "question":
                            part.evidence_ids = []
                            part.text = strip_markers(part.text)
                    if not repairs_remaining and answer.assessment:
                        users = [" ".join(m["content"].split()) for m in history if m["role"] == "user"]
                        kept = []
                        omitted = []
                        for i, requirement in enumerate(answer.assessment.requirements):
                            quote = " ".join(requirement.user_quote.split())
                            if quote and any(quote in user for user in users):
                                kept.append(requirement)
                            else:
                                omitted.append(f"assessment.requirements[{i}]")
                        if omitted:
                            # Remove the whole unmatched extraction. Never substitute
                            # a similar user sentence or retain an unsupported statement.
                            answer.assessment.requirements = kept
                            self.tools.event({"stage": "answer_detail_omitted", "fields": omitted})
                    structural_issues = self.structure_issues(answer, history, plan.intent)
                    issues = verify(answer, user_text, self.tools.inspected)
                    self.tools.event({"stage": "verification", "issues": structural_issues + issues})
                    uncited = [i for i, part in enumerate(answer.parts) if part.kind == "answer" and not part.evidence_ids]
                    if (structural_issues or issues) and not repairs_remaining and (
                            not structural_issues or structural_issues == [self.uncited_message(uncited)]):
                        # After the only repair, remove each failing paragraph, claim or
                        # list item whole rather than discard the answer. Never rewrite a
                        # field or attach another source; refuse if a required field empties.
                        trimmed = answer.model_copy(deep=True)
                        omitted = omit_failing_fields(trimmed, uncited, issues)
                        if omitted is not None:
                            self.tools.event({"stage": "answer_detail_omitted", "fields": omitted})
                            answer = trimmed
                            structural_issues = self.structure_issues(answer, history, plan.intent)
                            issues = verify(answer, user_text, self.tools.inspected)
                    if structural_issues or issues:
                        raise ValueError(" ".join(structural_issues + issues))
                    # Keep E aliases until all checks finish; resolve a copy.
                    validated = answer.model_copy(deep=True)
                    sources = self.validate(validated, history, plan.intent)
                    return validated, sources
                except (ValueError, KeyError, TypeError) as exc:
                    # Validator messages only; no provider bodies or hidden reasoning.
                    self.tools.event({"stage": "answer_rejected", "reason": str(exc)[:300]})
                    if not repairs_remaining:
                        raise AnswerValidationError("The generated answer failed quotation, citation or quantity validation") from exc
                    repairs_remaining -= 1
                    if raw:
                        active_messages.append({"role": "assistant", "content": raw})
                    if isinstance(exc, TruncatedAnswerError):
                        # Only after a cut-off: normal answers keep their full length.
                        active_messages.append({"role": "user", "content":
                            "The previous answer was cut off at the output limit. Return the complete JSON "
                            "more concisely: merge closely related parts, stay within 8 parts, and keep "
                            "every claim's citation."})
                        continue
                    active_messages.append({"role": "user", "content":
                        "Repair this answer using only the user statements and inspected evidence. "
                        "Remove unsupported claims; do not repair them by attaching an unrelated quote or citation. "
                        "Keep the customer's meaning and distinguish source findings from proposed actions. "
                        "Return the complete corrected JSON. Validation failure: " + str(exc)[:4000]})

        answer, sources = generate_checked(messages)
        assessment = answer.assessment
        has_gaps = any(e["stage"] == "answer_detail_omitted" for e in self.trace) or any(part.kind == "gap" for part in answer.parts) or bool(assessment and (
            assessment.gaps or assessment.unknowns or assessment.status != "ready_for_review"))
        answer.evidence_coverage = None
        answer.citation_status = ("present_with_gaps" if has_gaps else "present") if sources else "none"
        self.tools.event({"stage": "completed", "model_calls": self.model_calls,
            "tool_calls": self.tools.calls, "evidence_ids": list(sources),
            "timings_seconds": {"routing": round(routed_at-started_at, 3),
                "investigation": round(formatting_at-routed_at, 3),
                "answer_and_validation": round(time.monotonic()-formatting_at, 3),
                "total": round(time.monotonic()-started_at, 3)},
            "model": self.settings.model, "embedding_model": self.settings.embedding_model,
            "vision_model": self.settings.vision_model,
            "prompt_fingerprint": fingerprint({"investigator": system,
                "formatter": formatter_prompt, "method_shortlist": self.prompt("method_shortlist")})})
        return answer, sources

    def question_evidence(self, question):
        """Read the customer's full-question evidence in code, as one observation the agent starts from.

        It is recorded as a tool exchange so checkpoints restore its references
        like any other observation and the agent sees what is already read.
        """
        read, previews = self.tools.read_question_evidence(question)
        if not read and not previews:
            return []
        call = {"name": "search_knowledge", "args": {"query": question[:2000]}, "id": "question-evidence"}
        return [AIMessage(content="", tool_calls=[call]), ToolMessage(tool_call_id=call["id"], content=json.dumps({
            "read": read, "candidates": previews, "note": "Read in code for the customer's full wording. "
            "Use your turn only for a concrete gap these passages leave open."}, ensure_ascii=False))]

    @staticmethod
    def prompt(name):
        return Path(__file__).with_name("prompts").joinpath(name + ".md").read_text(encoding="utf-8")

    def add_method_background(self, history, deadline):
        """Ask which methods the assessment will propose, then read their source background in code.

        Search for the customer's application rarely ranks method pages, and the agent's
        turns are better spent on application evidence. Background is read only for the
        methods the answer will propose, never inferred from passing mentions.
        """
        # Keep the answer call and its single repair available.
        if self.model_calls >= self.settings.max_model_calls - 2 or time.monotonic() >= deadline:
            self.tools.event({"stage": "method_shortlist", "status": "skipped"})
            return
        self.model_calls += 1
        self.tools.event({"stage": "method_shortlist", "status": "started"})
        schema = tool_parameters(MethodShortlist)
        try:
            raw = self.gateway.answer_json([{"role": "system", "content": self.prompt("method_shortlist")},
                {"role": "user", "content": json.dumps({
                    "customer_messages": [m["content"] for m in history[-12:] if m["role"] == "user"],
                    "inspected_evidence": self.tools.inspected, "output_schema": schema}, ensure_ascii=False)}],
                schema, min(self.settings.provider_timeout, deadline - time.monotonic()), max_tokens=300)
            methods = MethodShortlist.model_validate_json(raw).methods
        except Exception as exc:
            # Optional enrichment: the answer still proceeds from the agent's own evidence.
            self.tools.event({"stage": "method_shortlist", "status": "failed", "error_type": type(exc).__name__})
            return
        added = self.tools.add_method_background(methods)
        self.tools.event({"stage": "background_added", "methods": methods, "references": added})

    def generation_request(self, history, plan):
        """Build the existing generation request independently of investigation."""
        prompt_dir = Path(__file__).with_name("prompts")
        schema = tool_parameters(ChatAnswer)
        # Routing owns intent; generation owns content, not reclassification.
        # Citation metadata is also computed in code.
        for field in ("intent", "citation_status", "evidence_coverage"):
            schema["properties"].pop(field, None)
            if field in schema.get("required", []):
                schema["required"].remove(field)
        if plan.intent == "assessment":
            # The transport schema should express the same plan requirement
            # as validation, rather than spending the repair on an avoidable null.
            case_schema = next(option for option in schema["properties"]["assessment"]["anyOf"]
                if option.get("type") == "object")
            schema["properties"]["assessment"] = case_schema
            if "assessment" not in schema["required"]:
                schema["required"].append("assessment")
            experiment_schema = next(option for option in case_schema["properties"]["experiment"]["anyOf"]
                if option.get("type") == "object")
            case_schema["properties"]["experiment"] = experiment_schema
            if "experiment" not in case_schema["required"]:
                case_schema["required"].append("experiment")
            experiment_schema["properties"]["objective"]["minLength"] = 1
            for field in ("steps", "criteria_to_confirm"):
                experiment_schema["properties"][field]["minItems"] = 1
                experiment_schema["properties"][field]["items"]["minLength"] = 1
        else:
            # Narrow Q&A cannot accidentally become a full assessment.
            schema["properties"]["assessment"] = {"type": "null"}
        references = list(self.tools.inspected)
        def constrain(value):
            if isinstance(value, dict):
                for name, child in value.items():
                    if name == "evidence_ids" and isinstance(child, dict):
                        child["items"]["enum"] = references
                    else:
                        constrain(child)
            elif isinstance(value, list):
                for child in value:
                    constrain(child)
        constrain(schema)
        messages = [{"role": "system", "content": prompt_dir.joinpath("assessment_answer.md").read_text(encoding="utf-8")},
            {"role": "user", "content": json.dumps({
                "inspected_evidence": self.tools.inspected,
                "earlier_customer_messages": [m["content"] for m in history[-12:-1] if m["role"] == "user"],
                "retrieval_query": plan.search_query, "intent": plan.intent,
                "experiment_requested": plan.wants_experiment,
                "latest_customer_message": history[-1]["content"],
                "output_schema": schema}, ensure_ascii=False)}]
        return messages, schema

    @staticmethod
    def uncited_message(missing):
        fields = [f"parts[{i}].evidence_ids" for i in missing]
        return ("Factual answer paragraphs require inspected supporting evidence. Missing fields: "
            + ", ".join(fields) + ". Cite the actual supporting passages for technical claims. "
            "Pure actions, questions or evidence gaps must use the corresponding part kind, "
            "not an unrelated decorative citation.")

    def validate(self, answer, history, intent):
        issues = self.structure_issues(answer, history, intent)
        if issues:
            raise ValueError(" ".join(issues))
        return self.resolve_references(answer)

    def structure_issues(self, answer, history, intent):
        issues = []
        if answer.intent != intent:
            issues.append("Answer intent must match the routed request.")
        if intent == "assessment" and answer.assessment is None:
            issues.append("A customer use case requires a structured assessment.")
        if intent == "qa" and answer.assessment is not None:
            issues.append("A narrow question does not require a full case assessment.")
        if all(part.kind == "question" for part in answer.parts):
            issues.append("parts: answer the latest customer message with a supported answer, "
                "an explicit evidence gap, or a requested proposal. Clarification questions "
                "may accompany that response but cannot replace it. Do not invent evidence.")
        missing = [i for i, part in enumerate(answer.parts) if part.kind == "answer" and not part.evidence_ids]
        if missing:
            issues.append(self.uncited_message(missing))
        references = [ref for part in answer.parts for ref in part.evidence_ids]
        assessment = answer.assessment
        if assessment:
            users = [" ".join(m["content"].split()) for m in history if m["role"] == "user"]
            unmatched = [f"assessment.requirements[{i}].user_quote"
                for i, requirement in enumerate(assessment.requirements)
                if not requirement.user_quote.strip() or not any(
                    " ".join(requirement.user_quote.split()) in user for user in users)]
            if unmatched:
                issues.append("Each requirement must quote an actual user statement. Unmatched fields: "
                    + ", ".join(unmatched) + ". Copy an exact user quote or omit that requirement; "
                    "do not substitute a similar sentence or change the customer's meaning.")
            experiment = assessment.experiment
            if (not experiment or not experiment.objective.strip()
                    or not experiment.steps or not all(step.strip() for step in experiment.steps)
                    or not experiment.criteria_to_confirm
                    or not all(criterion.strip() for criterion in experiment.criteria_to_confirm)):
                issues.append("Every customer assessment needs a first-study objective, concrete steps "
                    "and criteria to confirm, even while awaiting clarification. Make the study conditional "
                    "or information-gathering when final method selection is not yet supported.")
            if assessment.status == "ready_for_review" and not assessment.candidates:
                issues.append("A reviewable assessment requires evidence-supported candidates.")
            references += [ref for c in assessment.candidates for ref in c.evidence_ids]
            references += [ref for c in assessment.candidates for claim in c.claims for ref in claim.evidence_ids]
        fields = {path for path, parent, key in prose_fields(answer.model_dump()) if parent[key].strip()}
        for citation in answer.citations:
            if citation.field not in fields:
                issues.append("A field citation must identify an existing generated prose field: " + citation.field)
            references.extend(citation.evidence_ids)
        if any(ref not in self.tools.inspected for ref in references):
            issues.append("Only inspected evidence references may be cited.")
        return issues

    def resolve_references(self, answer):
        """Map E aliases to evidence IDs; call only after structure_issues returned none."""
        references = [ref for part in answer.parts for ref in part.evidence_ids]
        if answer.assessment:
            references += [ref for c in answer.assessment.candidates for ref in c.evidence_ids]
            references += [ref for c in answer.assessment.candidates for claim in c.claims for ref in claim.evidence_ids]
        references += [ref for citation in answer.citations for ref in citation.evidence_ids]
        assessment = answer.assessment
        sources = {self.tools.aliases[ref]: self.retrieval.context(self.tools.aliases[ref])
            for ref in references}
        # Paragraph markers become the reader-facing source numbers; no alias is shown.
        number_markers(answer, self.tools.aliases, list(sources))
        for part in answer.parts:
            part.evidence_ids = [self.tools.aliases[ref] for ref in part.evidence_ids]
        for citation in answer.citations:
            citation.evidence_ids = [self.tools.aliases[ref] for ref in citation.evidence_ids]
        if assessment:
            for candidate in assessment.candidates:
                candidate.evidence_ids = [self.tools.aliases[ref] for ref in candidate.evidence_ids]
                for claim in candidate.claims:
                    claim.evidence_ids = [self.tools.aliases[ref] for ref in claim.evidence_ids]
        return sources
