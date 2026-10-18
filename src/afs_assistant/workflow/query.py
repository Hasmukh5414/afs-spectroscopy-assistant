"""Bounded conversation routing and evidence relevance checks for normal chat."""
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from afs_assistant.core.models import Contract
from afs_assistant.core.tool_schema import tool_parameters


class QueryPlan(Contract):
    route: Literal["technical", "out_of_scope", "social", "clarify"]
    search_query: str = Field(max_length=2000)
    wants_experiment: bool
    intent: Literal["qa", "assessment"] = "qa"


def prompt(name):
    return Path(__file__).with_name("prompts").joinpath(name + ".md").read_text(encoding="utf-8")


def plan_query(history, gateway, timeout) -> QueryPlan:
    # Preserve the initial application along with recent corrections. Assistant
    # text is not a source of customer requirements or a new retrieval objective.
    users = [message["content"][:2000] for message in history if message["role"] == "user"]
    if len(users) > 12:
        users = [users[0], *users[-11:]]
    schema = tool_parameters(QueryPlan)
    result = gateway.answer_json([
        {"role": "system", "content": prompt("route")},
        {"role": "user", "content": json.dumps({"earlier_user_messages": users[:-1], "latest_user_message": users[-1], "output_schema": schema}, ensure_ascii=False)},
    ], schema, timeout, max_tokens=600)
    plan = QueryPlan.model_validate_json(result)
    if plan.route == "technical" and not plan.search_query.strip():
        raise ValueError("Technical query plan is missing its search query")
    return plan
