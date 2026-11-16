"""Exact decimal / rational helpers for monetary amounts.

XBRL amounts are decimal strings.  Converting them to binary ``float`` before
comparison silently merges distinct values once they exceed 2**53 (for example
``9007199254740993`` and ``9007199254740992``) and also destroys the half-unit
resolution of ``decimals=0`` for large amounts.  Everything in the checking
pipeline therefore stays exact: values and rounding bounds are parsed with
``Decimal`` and represented as :class:`fractions.Fraction` while solving.
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Union

ExactAmount = Union[int, float, str]


def to_fraction(value: Any) -> Fraction:
    """Convert a stored/parsed amount to an exact rational.

    Floats (legacy rows, unit tests) are interpreted through their shortest
    decimal representation, which is the amount the caller meant to express.
    Strings may be decimal literals (including exponent form) or ``p/q``
    rational literals used for non-terminating witnesses.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):  # bool is an int subclass; never an amount
        raise TypeError("boolean is not a monetary amount")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("monetary amount must be finite")
        return Fraction(Decimal(repr(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("monetary amount must be finite")
        return Fraction(value)
    if isinstance(value, str):
        text = value.strip()
        # JSON's ``repr`` may emit exponent notation (e.g. ``1e-07``); Decimal
        # parses both that and plain decimal literals exactly.
        try:
            return Fraction(Decimal(text))
        except (InvalidOperation, ValueError):
            try:
                return Fraction(text)
            except (ValueError, ZeroDivisionError) as exc:
                raise ValueError(f"invalid monetary amount {value!r}") from exc
    raise TypeError(f"unsupported monetary amount type {type(value).__name__}")


def decimals_interval(value: Fraction, decimals: object) -> tuple[Fraction, Fraction]:
    """Closed true-value interval for a fact with the given ``decimals``.

    ``decimals=d`` gives ``[value - 0.5*10^-d, value + 0.5*10^-d]``; ``INF``
    pins the value exactly.  Arithmetic is rational, so no rounding occurs even
    for amounts far beyond binary floating-point precision.
    """
    if decimals == "INF":
        return value, value
    d = int(decimals)  # type: ignore[arg-type]
    if d >= 0:
        half = Fraction(1, 2 * 10 ** d)
    else:
        half = Fraction(10 ** (-d), 2)
    return value - half, value + half


def _finite_decimal_literal(value: Fraction) -> str | None:
    """Return an exact decimal literal, or ``None`` for a non-terminating one."""
    numerator, denominator = value.numerator, value.denominator
    sign = "-" if numerator < 0 else ""
    numerator = abs(numerator)
    reduced = denominator
    twos = fives = 0
    while reduced % 2 == 0:
        reduced //= 2
        twos += 1
    while reduced % 5 == 0:
        reduced //= 5
        fives += 1
    if reduced != 1:
        return None
    scale = max(twos, fives)
    scaled = numerator * 10 ** scale // denominator
    digits = str(scaled)
    if scale == 0:
        return f"{sign}{digits}"
    if len(digits) <= scale:
        digits = "0" * (scale - len(digits) + 1) + digits
    return f"{sign}{digits[:-scale]}.{digits[-scale:]}"


def amount_out(value: Any) -> ExactAmount:
    """Serialize an exact amount without losing digits.

    * integers are emitted as JSON integers (Python/JSON preserve all digits);
    * values whose exact decimal literal round-trips through a binary ``float``
      are emitted as JSON numbers (the literal itself is the exact decimal);
    * decimal values that a binary float cannot represent (e.g.
      ``9007199254740992.5``) are emitted as exact decimal strings;
    * non-terminating rational witnesses are emitted as ``"p/q"`` strings.

    The check itself never relies on these approximations; this only chooses a
    lossless JSON representation.
    """
    fraction = to_fraction(value)
    if fraction.denominator == 1:
        return fraction.numerator
    literal = _finite_decimal_literal(fraction)
    if literal is None:
        return f"{fraction.numerator}/{fraction.denominator}"
    try:
        as_float = float(fraction)
    except (OverflowError, ValueError):
        return literal
    if math.isfinite(as_float):
        # The JSON token produced for this float is its shortest repr; use the
        # float only when that token denotes exactly the same decimal amount.
        try:
            if Decimal(repr(as_float)) == Decimal(literal):
                return as_float
        except InvalidOperation:  # pragma: no cover - defensive
            pass
    return literal
