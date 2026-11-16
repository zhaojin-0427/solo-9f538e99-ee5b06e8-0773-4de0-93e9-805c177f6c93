"""Rounding-aware reconciliation over exact decimal amounts.

Each numeric fact with ``decimals=d`` bounds its true value to the closed
interval ``[value-0.5*10^-d, value+0.5*10^-d]``; ``decimals=INF`` pins the
value exactly.  All equations share the same true values, so checking the
filing is a linear feasibility problem: find one vector of true values inside
all the boxes that satisfies every weighted summation equation.

Amounts are decimal strings and are solved as exact rationals (see
:mod:`app.exact_lp`).  Nothing is rounded to binary ``float`` before the
feasibility decision, so ``9007199254740993`` and ``9007199254740992`` are
distinct, and a returned witness lies exactly inside the original interval.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Optional

from .amounts import amount_out, to_fraction
from .exact_lp import find_feasible


def _bounds(facts_by_concept: dict, concepts: list[str]
            ) -> tuple[list[Fraction], list[Fraction]]:
    lowers: list[Fraction] = []
    uppers: list[Fraction] = []
    for c in concepts:
        fact = facts_by_concept[c]
        lo = to_fraction(fact["lower"])
        hi = to_fraction(fact["upper"])
        # Guard against legacy rows whose interval was stored reversed.
        if lo > hi:
            lo, hi = hi, lo
        lowers.append(lo)
        uppers.append(hi)
    return lowers, uppers


def _solve(concepts: list[str], lowers: list[Fraction], uppers: list[Fraction],
           equations: list[dict], maximize: bool = True
           ) -> Optional[list[Fraction]]:
    """Return an exact feasible true-value vector, or None when infeasible."""
    index = {c: i for i, c in enumerate(concepts)}
    coefficients = []
    for eq in equations:
        row: dict[int, int] = {}
        for concept, coef in eq["coefficients"].items():
            row[index[concept]] = row.get(index[concept], 0) + int(coef)
        coefficients.append({j: coef for j, coef in row.items() if coef})
    return find_feasible(
        coefficients,
        [Fraction(0)] * len(equations),
        lowers,
        uppers,
        maximize=maximize,
    )


def _residual(eq: dict, x: dict[str, Fraction]) -> Fraction:
    return sum(coef * x[c] for c, coef in eq["coefficients"].items())


def _witness_rows(concepts: list[str], x: list[Fraction], facts_by_concept: dict
                  ) -> list[dict]:
    rows = []
    for concept, true_value in zip(concepts, x):
        fact = facts_by_concept[concept]
        rows.append({
            "concept": concept,
            "reported_value": fact["value"],
            "decimals": fact["decimals"],
            "true_value": amount_out(true_value),
            "lower": fact["lower"],
            "upper": fact["upper"],
        })
    return rows


def _all_referenced(facts_by_concept: dict, equations: list[dict]) -> list[str]:
    return sorted({c for eq in equations for c in eq["coefficients"]})


def check_facts(facts: list[dict], equations: list[dict]) -> dict:
    facts_by_concept = {f["concept"]: f for f in facts}

    # A filing is "pending" as soon as any equation references a fact the
    # instance does not provide; no solver call is attempted.
    missing = sorted({
        c for eq in equations for c in eq["coefficients"] if c not in facts_by_concept
    })
    if missing:
        return {"status": "pending", "missing_facts": missing}

    referenced = _all_referenced(facts_by_concept, equations)
    lowers, uppers = _bounds(facts_by_concept, referenced)
    solution = _solve(referenced, lowers, uppers, equations, maximize=True)

    if solution is not None:
        x_map = dict(zip(referenced, solution))
        # ``find_feasible`` already verifies exactly; assert the public contract
        # once more so a witness can never claim a value outside its interval
        # or a non-zero equation residual.
        for c in referenced:
            fact = facts_by_concept[c]
            if not (to_fraction(fact["lower"]) <= x_map[c]
                    <= to_fraction(fact["upper"])):
                return _infeasible(referenced, lowers, uppers, equations,
                                   facts_by_concept)
        for eq in equations:
            if _residual(eq, x_map) != 0:
                return _infeasible(referenced, lowers, uppers, equations,
                                   facts_by_concept)

        all_rows = _witness_rows(referenced, solution, facts_by_concept)
        # unreferenced facts keep their own admissible true values
        referenced_set = set(referenced)
        for fact in facts:
            if fact["concept"] not in referenced_set:
                all_rows.append({
                    "concept": fact["concept"],
                    "reported_value": fact["value"],
                    "decimals": fact["decimals"],
                    "true_value": fact["value"],
                    "lower": fact["lower"],
                    "upper": fact["upper"],
                })
        return {
            "status": "feasible",
            "witness": all_rows,
            "max_abs_residual": amount_out(
                max((abs(_residual(eq, x_map)) for eq in equations),
                    default=Fraction(0))
            ),
        }

    return _infeasible(referenced, lowers, uppers, equations, facts_by_concept)


def _minimal_infeasible_groups(concepts: list[str], lowers: list[Fraction],
                               uppers: list[Fraction],
                               equations: list[dict]) -> list[list[dict]]:
    """Partition the equations into disjoint irreducibly infeasible groups.

    Each returned group stays infeasible, but dropping any single equation from
    it makes that group feasible — a tight set of mutually contradictory
    facts and relationships.
    """
    remaining = list(equations)
    groups: list[list[dict]] = []
    while _solve(concepts, lowers, uppers, remaining) is None:
        keep = list(remaining)
        for eq in list(remaining):
            if len(keep) == 1:
                break
            trial = [e for e in keep if e is not eq]
            if _solve(concepts, lowers, uppers, trial) is None:
                keep = trial
        groups.append(keep)
        remaining = [e for e in remaining if e not in keep]
        if not remaining:
            break
    return groups


def _infeasible(concepts: list[str], lowers: list[Fraction],
                uppers: list[Fraction], equations: list[dict],
                facts_by_concept: dict) -> dict:
    groups = _minimal_infeasible_groups(concepts, lowers, uppers, equations)

    # A best-effort witness for the conflict facts: interval midpoints, so the
    # response still shows concrete amounts and the residual of each equation.
    # Midpoints are exact and always inside the original closed interval.
    mid_points = [(lo + hi) / 2 for lo, hi in zip(lowers, uppers)]
    x_map = dict(zip(concepts, mid_points))
    rows = _witness_rows(concepts, mid_points, facts_by_concept)
    row_by_concept = {r["concept"]: r for r in rows}

    conflicts = []
    involved: set[str] = set()
    for group in groups:
        for eq in group:
            involved.update(eq["coefficients"].keys())
            conflicts.append({
                "equation": eq,
                "residual_at_witness": amount_out(_residual(eq, x_map)),
                "facts": [row_by_concept[c] for c in eq["coefficients"]],
            })
    return {
        "status": "infeasible",
        "conflicts": conflicts,
        "conflict_facts": [row_by_concept[c] for c in sorted(involved)],
    }
