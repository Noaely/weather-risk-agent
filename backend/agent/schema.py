"""The response contract (submit_answer input), validated with Pydantic in the backend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# The UI shows the hub scores table automatically when the answer references at least this many hubs
# (rankings and comparisons). The same rule is stated in the system prompt, so the model knows when to
# keep its text short instead of repeating the table. One constant, used by both.
TABLE_MIN_HUBS = 2


class ScoreItem(BaseModel):
    hub_id: str = Field(description="hub_id exactly as returned by a tool, e.g. 'kansas_city'")
    name: str = Field(description="what the value is, e.g. 'flood score', 'overall score', "
                                  "'measurable snow days 2025 (% of observed days)'")
    value: float = Field(description="the number exactly as a tool returned it - never computed by you")
    unit: str = Field(description="e.g. '0-100 score', '% of days', 'days/yr', 'events/yr', 'mm'")


class AgentAnswer(BaseModel):
    answer: str = Field(description="The reply shown to the analyst. Plain, direct, a few short paragraphs at most.")
    hubs_referenced: list[str] = Field(description="hub_ids the answer is about (empty if none)")
    scores: list[ScoreItem] = Field(description="every number the answer relies on, copied from tool results")
    reasoning: str = Field(description="how the tool results lead to the answer, in 1-4 sentences")
    assumptions: list[str] = Field(description="thresholds, periods, definitions and data caveats that apply")
    confidence: Literal["high", "medium", "low"] = Field(
        description="high: tools answered it directly; medium: answer depends on stated caveats; "
                    "low: partly or wholly unanswerable with the data")
    data_sources: list[str] = Field(description="data sources behind the numbers, as named in the tool results")


def submit_answer_schema() -> dict:
    """JSON schema for strict tool use: every object needs additionalProperties: false."""
    schema = AgentAnswer.model_json_schema()

    def close(node: dict) -> None:
        if node.get("type") == "object":
            node["additionalProperties"] = False
        for v in node.get("properties", {}).values():
            close(v)
        for v in node.get("$defs", {}).values():
            close(v)
        if isinstance(node.get("items"), dict):
            close(node["items"])

    close(schema)
    schema.pop("title", None)
    return schema
