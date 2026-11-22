"""Tool definitions sent to Claude, and the dispatcher that runs them.

All tools are strict (schema-valid arguments guaranteed). Enums limit hazards, regions and metrics
to what we can actually compute, so an out-of-scope request ("earthquake") cannot be passed in;
the system prompt tells the model to say it is out of scope instead.
The list is built once and never changes during a session (prompt caching + preserved thinking
both require identical tools on every request).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from backend.agent.schema import submit_answer_schema
from backend.scoring.config import HAZARDS
from backend.tools.hubs import list_hubs
from backend.tools.metric import METRICS, get_hub_metric
from backend.tools.scores import compare_hubs, explain_score, rank_hubs

REGIONS = ["Midwest", "South", "West"]
HAZARD_NAMES = list(HAZARDS)                       # winter, hurricane, flood
RANK_BY = ["overall", "multi_hazard", *HAZARD_NAMES]


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS: list[dict] = [
    {
        "name": "list_hubs",
        "description": "List the client's hubs (id, name, state, region, weather station, NRI county). "
                       "Use to check which hubs exist or which belong to a region.",
        "input_schema": _obj({"region": {"type": "string", "enum": REGIONS}}, []),
    },
    {
        "name": "rank_hubs",
        "description": "Rank hubs by a score, highest risk first, optionally within one region. "
                       "'overall' = worst single hazard (the main score); 'multi_hazard' = average of the "
                       "three hazards; or one hazard. Equal scores share a rank. Use for 'which hubs are "
                       "most exposed to X' questions.",
        "input_schema": _obj({"hazard": {"type": "string", "enum": RANK_BY},
                              "region": {"type": "string", "enum": REGIONS}}, ["hazard"]),
    },
    {
        "name": "compare_hubs",
        "description": "Side-by-side scores for several hubs: overall, multi-hazard, and for each requested "
                       "hazard its score, level and metric breakdown (raw values, units, weights, sources). "
                       "One call covers all hubs and hazards in a comparison question.",
        "input_schema": _obj({"hubs": {"type": "array", "items": {"type": "string"},
                                       "description": "hub names as the user wrote them"},
                              "hazards": {"type": "array", "items": {"type": "string", "enum": HAZARD_NAMES},
                                          "description": "omit for all hazards"}}, ["hubs"]),
    },
    {
        "name": "get_hub_metric",
        "description": "One factual weather or NRI metric for one hub, for a single calendar year or (year "
                       "omitted) the 10-year scoring window. Day-count metrics return qualifying days, "
                       "observed days, % of observed days, days/yr, coverage and the threshold used. "
                       "measurable_snow_days uses the NWS definition (>= 0.1 in); heavy_snow_days >= 1 in "
                       "(the scoring threshold). Use for 'how often / how much / what % of days' questions.",
        "input_schema": _obj({"hub": {"type": "string"},
                              "metric": {"type": "string", "enum": METRICS},
                              "year": {"type": "integer", "description": "calendar year, e.g. 2025"}},
                             ["hub", "metric"]),
    },
    {
        "name": "explain_score",
        "description": "Full breakdown of a hub's scores: each metric's raw value, unit, 0-100 score, weight, "
                       "anchors and source; the hub's rank; context data that is NOT scored (FEMA NRI tornado, "
                       "hail and ice storm frequency with US county percentiles; model wind-gust days); and "
                       "data caveats. Use for 'why' questions.",
        "input_schema": _obj({"hub": {"type": "string"},
                              "hazard": {"type": "string", "enum": HAZARD_NAMES,
                                         "description": "omit to explain all hazards"}}, ["hub"]),
    },
    {
        "name": "submit_answer",
        "description": "Deliver the final answer to the analyst. Call exactly once, as the last step of every "
                       "reply, including when the answer is 'I don't know' or 'out of scope'.",
        "input_schema": submit_answer_schema(),
    },
]
for _t in TOOLS:
    _t["strict"] = True

HANDLERS: dict[str, Callable[..., dict]] = {
    "list_hubs": list_hubs, "rank_hubs": rank_hubs, "compare_hubs": compare_hubs,
    "get_hub_metric": get_hub_metric, "explain_score": explain_score,
}


def run_tool(conn: sqlite3.Connection, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run a data tool; returns (JSON text, is_error). Errors go back to the model, not up the stack."""
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name!r}", "available": list(HANDLERS)}), True
    try:
        return json.dumps(handler(conn, **args)), False
    except Exception as e:  # a tool bug must not crash the chat; the model sees the error and can recover
        return json.dumps({"error": f"{type(e).__name__}: {e}"}), True
