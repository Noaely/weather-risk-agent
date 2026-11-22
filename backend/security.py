"""Access control for a public deployment: shared access code, rate limits, daily spend cap.

No user accounts: one code, shared privately with reviewers. Everything is enforced by the backend on
every /chat request, so calling the API directly (without the UI) gets the same checks.

    ACCESS_CODE           the shared code. Required when APP_ENV=production (the app refuses to start
                          without it); when unset outside production, /chat is open (local development).
    RATE_PER_IP_HOUR      questions per client IP per rolling hour      (default 40)
    RATE_GLOBAL_HOUR      questions across all clients per rolling hour (default 60)
    FAILED_CODES_PER_IP_HOUR  wrong codes per IP per hour before that IP is blocked for the hour (default 10)
    DAILY_SPEND_CAP_USD   Claude API spend per UTC day before /chat stops answering (default 1.00)

State is in memory: correct for the single instance we run; it resets on restart (a restart therefore
also resets the day's spend counter - acceptable for a demo, noted in the limitations).
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Request

HOUR = 3600.0


class ConfigError(RuntimeError):
    pass


class SlidingWindow:
    """Counts events per key over the last `window` seconds."""

    def __init__(self, limit: int, window: float = HOUR) -> None:
        self.limit, self.window = limit, window
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._events[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record an event for `key` if under the limit; False (nothing recorded) if over it."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._trim(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def count(self, key: str, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        with self._lock:
            return len(self._trim(key, now))

    def add(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._trim(key, now).append(now)


def client_ip(request: Request) -> tuple[str, str]:
    """(ip, source). Uses True-Client-IP (set by Cloudflare in front of Render) when present, else the
    socket address. X-Forwarded-For is NOT used: Render appends to it without clearing it, so its first
    entry can be forged by the caller. If only the socket (proxy) address is available, all clients share
    one key - the per-IP limit gets stricter, never looser."""
    tci = request.headers.get("true-client-ip")
    if tci:
        return tci.strip(), "true-client-ip"
    return (request.client.host if request.client else "unknown"), "socket"


@dataclass
class Decision:
    ok: bool
    status: int = 200
    message: str = ""


@dataclass
class Guard:
    access_code: str | None
    per_ip_hour: int = 40
    global_hour: int = 60
    failed_codes_per_ip_hour: int = 10
    daily_cap_usd: float = 1.00
    _per_ip: SlidingWindow = field(init=False)
    _global: SlidingWindow = field(init=False)
    _failed: SlidingWindow = field(init=False)
    _spend: dict[str, float] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self._per_ip = SlidingWindow(self.per_ip_hour)
        self._global = SlidingWindow(self.global_hour)
        self._failed = SlidingWindow(self.failed_codes_per_ip_hour)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Guard":
        env = dict(os.environ) if env is None else env
        code = (env.get("ACCESS_CODE") or "").strip() or None
        if env.get("APP_ENV") == "production":
            if not code:
                raise ConfigError("APP_ENV=production requires ACCESS_CODE (refusing to start an open API).")
            if len(code) < 8:
                raise ConfigError("ACCESS_CODE must be at least 8 characters.")
        return cls(
            access_code=code,
            per_ip_hour=int(env.get("RATE_PER_IP_HOUR", 40)),
            global_hour=int(env.get("RATE_GLOBAL_HOUR", 60)),
            failed_codes_per_ip_hour=int(env.get("FAILED_CODES_PER_IP_HOUR", 10)),
            daily_cap_usd=float(env.get("DAILY_SPEND_CAP_USD", 1.00)),
        )

    @property
    def code_required(self) -> bool:
        return self.access_code is not None

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def spent_today(self) -> float:
        with self._lock:
            return self._spend.get(self._today(), 0.0)

    def record_spend(self, usd: float) -> None:
        with self._lock:
            day = self._today()
            self._spend = {day: self._spend.get(day, 0.0) + usd}  # keep only today

    def check_code(self, ip: str, supplied_code: str | None) -> Decision:
        """Blocked IP first, then the code (constant-time compare). Wrong codes count toward the block."""
        if not self.code_required:
            return Decision(True)
        if self._failed.count(ip) >= self.failed_codes_per_ip_hour:
            return Decision(False, 429, "Too many wrong access codes from this address. Try again in an hour.")
        if not supplied_code or not hmac.compare_digest(supplied_code.encode(), self.access_code.encode()):
            self._failed.add(ip)
            return Decision(False, 401, "Access code missing or wrong.")
        return Decision(True)

    def check(self, ip: str, supplied_code: str | None) -> Decision:
        """Run before every /chat request, in this order: blocked IP, code, spend cap, rate limits."""
        code = self.check_code(ip, supplied_code)
        if not code.ok:
            return code
        if self.spent_today() >= self.daily_cap_usd:
            return Decision(False, 429, "The demo's daily usage budget is used up. Please try again tomorrow (UTC).")
        if self._global.count("all") >= self.global_hour:
            return Decision(False, 429, "The demo is busy (hourly limit for all users reached). Please try again later.")
        if not self._per_ip.allow(ip):
            return Decision(False, 429, f"Hourly limit of {self.per_ip_hour} questions from this address reached. Please try again later.")
        self._global.add("all")
        return Decision(True)
