"""The calculation context of volatile functions.

One recalculation captures one clock instant, one session UTC offset and one
random seed. Every ``NOW``/``TODAY`` in the pass reads the same instant, and
every random draw comes from a stream keyed by ``(seed, cell key, call site,
draw)``, so a pass is reproducible from its context alone and does not depend
on evaluation order or worker count. The Rust engine implements the same rules
independently (``rust/src/volatile.rs``); see ``docs/volatile-functions.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import itertools
import os
import re
import secrets
import time

UNIX_EPOCH_SERIAL = 25_569
_MS_PER_DAY = 86_400_000
MAX_UTC_OFFSET_MINUTES = 14 * 60
_MASK = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15

#: Functions whose value can change without an edit. A formula calling one is
#: recalculated on every pass, and so is everything that depends on it.
VOLATILE_FUNCTIONS = frozenset({"NOW", "TODAY", "RAND", "RANDBETWEEN", "RANDARRAY"})
#: Random-number functions: each call site in a formula owns one stream.
RANDOM_FUNCTIONS = frozenset({"RAND", "RANDBETWEEN", "RANDARRAY"})

_INSTANT_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:\d{2})\Z"
)
_counter = itertools.count(1)


def parse_seed(text: str) -> int:
    """A seed is a decimal unsigned 64-bit integer."""
    if not isinstance(text, str) or not text or not text.isascii() or not text.isdigit():
        raise ValueError("seed must be a decimal integer")
    value = int(text)
    if value > _MASK:
        raise ValueError("seed must fit in 64 bits")
    return value


def check_offset(minutes: int) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("utc offset must be an integer number of minutes")
    if not -MAX_UTC_OFFSET_MINUTES <= minutes <= MAX_UTC_OFFSET_MINUTES:
        raise ValueError("utc offset must be within -840..=840 minutes")
    return minutes


def parse_instant(text: str) -> int:
    """Unix milliseconds from a decimal integer or an RFC 3339 instant.

    The instant must carry ``Z`` or an explicit ``+HH:MM``/``-HH:MM`` offset;
    a local time without one is refused, since its instant would depend on the
    machine.
    """
    text = text.strip()
    bad = ValueError(f"invalid instant {text!r}; use Unix ms or RFC 3339 with an offset")
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    match = _INSTANT_RE.fullmatch(text)
    if match is None:
        raise bad
    year, month, day, hour, minute, second = (int(part) for part in match.group(1, 2, 3, 4, 5, 6))
    fraction = match.group(7) or ""
    millis = int((fraction + "000")[:3])
    zone = match.group(8)
    if zone in {"Z", "z"}:
        offset = 0
    else:
        hours, minutes = int(zone[1:3]), int(zone[4:6])
        if hours > 14 or minutes > 59:
            raise bad
        offset = (hours * 60 + minutes) * (-1 if zone[0] == "-" else 1)
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        raise bad from None
    days = (moment.date().toordinal() - datetime(1970, 1, 1).toordinal())
    return days * _MS_PER_DAY + ((hour * 60 + minute) * 60 + second) * 1000 + millis - offset * 60_000


def fnv1a64(data: bytes) -> int:
    value = 0xCBF29CE484222325
    for byte in data:
        value ^= byte
        value = (value * 0x100000001B3) & _MASK
    return value


def mix64(value: int) -> int:
    """The SplitMix64 finalizer."""
    value &= _MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK
    return value ^ (value >> 31)


@dataclass(frozen=True, slots=True)
class CalcContext:
    """One recalculation's clock and seed.

    ``now_ms`` is the captured instant in Unix epoch milliseconds (UTC);
    ``utc_offset_minutes`` is the session offset, east positive; ``seed`` keys
    the pass's random streams.
    """

    now_ms: int
    utc_offset_minutes: int = 0
    seed: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.now_ms, bool) or not isinstance(self.now_ms, int):
            raise ValueError("now_ms must be an integer")
        check_offset(self.utc_offset_minutes)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed <= _MASK:
            raise ValueError("seed must be an unsigned 64-bit integer")

    @classmethod
    def capture(cls, utc_offset_minutes: int = 0) -> CalcContext:
        """Capture a context for a new pass.

        ``FORGE_CALC_NOW`` (Unix ms or RFC 3339 with an offset),
        ``FORGE_CALC_SEED`` (decimal u64) and ``FORGE_CALC_UTC_OFFSET_MINUTES``
        override the clock, the seed and the session offset. An invalid
        override is ignored rather than guessed.
        """
        now_ms = None
        if "FORGE_CALC_NOW" in os.environ:
            try:
                now_ms = parse_instant(os.environ["FORGE_CALC_NOW"])
            except ValueError:
                now_ms = None
        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000
        offset = utc_offset_minutes
        if "FORGE_CALC_UTC_OFFSET_MINUTES" in os.environ:
            try:
                offset = check_offset(int(os.environ["FORGE_CALC_UTC_OFFSET_MINUTES"].strip()))
            except ValueError:
                offset = utc_offset_minutes
        seed = None
        if "FORGE_CALC_SEED" in os.environ:
            try:
                seed = parse_seed(os.environ["FORGE_CALC_SEED"].strip())
            except ValueError:
                seed = None
        if seed is None:
            seed = secrets.randbits(64) ^ mix64(next(_counter))
        return cls(now_ms, offset, seed)

    @classmethod
    def from_dict(cls, data: dict) -> CalcContext:
        if not isinstance(data, dict) or set(data) != {"now_ms", "utc_offset_minutes", "seed"}:
            raise ValueError("calc context needs exactly now_ms, utc_offset_minutes and seed")
        return cls(data["now_ms"], data["utc_offset_minutes"], parse_seed(data["seed"]))

    def to_dict(self) -> dict:
        return {"now_ms": self.now_ms, "utc_offset_minutes": self.utc_offset_minutes, "seed": str(self.seed)}

    def _local_ms(self) -> int:
        return self.now_ms + self.utc_offset_minutes * 60_000

    def now_serial(self) -> float:
        """``NOW()``: the 1900-system serial with the time of day as a fraction."""
        return float(UNIX_EPOCH_SERIAL) + float(self._local_ms()) / float(_MS_PER_DAY)

    def today_serial(self) -> int:
        """``TODAY()``: the local calendar day, on integers."""
        return UNIX_EPOCH_SERIAL + self._local_ms() // _MS_PER_DAY

    def uniform(self, cell_key: str, site: int, draw: int) -> float:
        """The ``draw``-th value in ``[0, 1)`` of call site ``site`` at ``cell_key``."""
        value = self.seed ^ fnv1a64(cell_key.encode("utf-8"))
        value = mix64((value + _GOLDEN * (site + 1)) & _MASK)
        value = mix64((value + _GOLDEN * (draw + 1)) & _MASK)
        return (value >> 11) * (1.0 / 9_007_199_254_740_992.0)


__all__ = [
    "CalcContext",
    "RANDOM_FUNCTIONS",
    "VOLATILE_FUNCTIONS",
    "check_offset",
    "parse_instant",
    "parse_seed",
]
