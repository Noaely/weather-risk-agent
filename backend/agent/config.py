"""Agent settings from .env. Switch model / effort / thinking without code changes.

AGENT_MODEL     claude-sonnet-5-5 (default, demo latency) or claude-opus-5-5
AGENT_EFFORT    low | medium | high   (medium = Anthropic's starting point for multi-step tool use)
AGENT_THINKING  adaptive | between_tools
                adaptive: the model decides how much to think (the only option on Opus 5.5).
                between_tools: no extended thinking at all, Sonnet 5.5 only, effort high or below.
AGENT_FALLBACKS on | off   server-side refusal fallback (beta). On by default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from backend.settings import ROOT

load_dotenv(ROOT / ".env")

SONNET = "claude-sonnet-5-5"


@dataclass(frozen=True)
class AgentConfig:
    model: str
    effort: str
    thinking: str
    fallbacks: bool
    max_rounds: int = 8          # model calls per user question before giving up
    max_tokens: int = 16000      # per model call; thinking counts toward it

    def validate(self) -> None:
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"AGENT_EFFORT={self.effort!r} is not a valid effort level")
        if self.thinking not in {"adaptive", "between_tools"}:
            raise ValueError(f"AGENT_THINKING={self.thinking!r}: use 'adaptive' or 'between_tools'")
        if self.thinking == "between_tools":
            # The API rejects these combinations with a 400; fail at startup instead of mid-demo.
            if self.model != SONNET:
                raise ValueError("AGENT_THINKING=between_tools is only accepted by claude-sonnet-5-5")
            if self.effort in {"xhigh", "max"}:
                raise ValueError("AGENT_THINKING=between_tools requires AGENT_EFFORT high or below")


def load_config(**overrides) -> AgentConfig:
    cfg = AgentConfig(
        model=os.getenv("AGENT_MODEL", SONNET),
        effort=os.getenv("AGENT_EFFORT", "medium"),
        thinking=os.getenv("AGENT_THINKING", "adaptive"),
        fallbacks=os.getenv("AGENT_FALLBACKS", "on").lower() != "off",
    )
    if overrides:
        cfg = AgentConfig(**{**cfg.__dict__, **overrides})
    cfg.validate()
    return cfg
