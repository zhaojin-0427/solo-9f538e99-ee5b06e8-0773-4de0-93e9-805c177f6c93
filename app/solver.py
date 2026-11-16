"""Rounding-aware reconciliation.

Each numeric fact with ``decimals=d`` bounds its true value to the closed
interval ``[value-0.5*10^-d, value+0.5*10^-d]``; ``decimals=INF`` pins the
value exactly.  All equations share the same true values, so checking the
filing is a linear feasibility problem: find one vector of true values inside
all the boxes that satisfies every weighted summation equation.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.optimize import linprog

from . import config
from .errors import ApiError


def _bounds(facts_by_concept: dict, concepts: list[str]) -> list[tuple[float, float]]:
    bounds = []
    for c in concepts:
        f = facts_by_concept[c]
        lo, hi = f["lower"], f["upper"]
        # guard against tiny float drift so the reported value stays feasible
        if lo > hi:
            lo, hi = hi, lo
        bounds.append((lo, hi))
    return bounds


def _solve(concepts: list[str], bounds: list[tuple[float, float]],
           equations: list[dict], maximize: bool = False) -> Optional[np.ndarray]:
    """Return a feasible true-value vector, or None when infeasible.

    With ``maximize=True`` the objective pushes every variable to its upper
    bound, yielding a deterministic corner-point witness (and a boundary point
    when the only solutions sit on the edge of a rounding interval).
    """
    n = len(concepts)
    idx = {c: i for i, c in enumerate(concepts)}
    A_eq = np.zeros((len(equations), n))
    b_eq = np.zeros(len(equations))
    for r, eq in enumerate(equations):
        for concept, coef in eq["coefficients"].items():
            A_eq[r, idx[concept]] = coef
    # feasibility with a deterministic corner objective
    c = -np.ones(n) if maximize else np.zeros(n)
    res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.success:
        return res.x
    if res.status in (2, 3):  # infeasible / unbounded(impossible with bounded vars)
        return None
    raise ApiError(500, "solver_error", f"feasibility solver failed: {res.message}")


def _residual(eq: dict, x: dict[str, float]) -> float:
    return float(sum(coef * x[c] for c, coef in eq["coefficients"].items()))


def _witness_rows(concepts: list[str], x: np.ndarray, facts_by_concept: dict) -> list[dict]:
    rows = []
    for i, concept in enumerate(concepts):
        f = facts_by_concept[concept]
        true_value = 0.0 if abs(float(x[i])) < config.WITNESS_TOL else float(x[i])
        rows.append({
            "concept": concept,
            "reported_value": f["value"],
            "decimals": f["decimals"],
            "true_value": round(true_value, 10),
            "lower": f["lower"],
            "upper": f["upper"],
        })
    return rows


def check_facts(facts: list[dict], equations: list[dict]) -> dict:
    facts_by_concept = {f["concept"]: f for f in facts}

    # A filing is "pending" as soon as any equation references a fact the
    # instance does not provide; no solver call is attempted.
    missing = sorted({
        c for eq in equations for c in eq["coefficients"] if c not in facts_by_concept
    })
    if missing:
        return {"status": "pending", "missing_facts": missing}

    referenced = sorted({c for eq in equations for c in eq["coefficients"]})
    bounds = _bounds(facts_by_concept, referenced)
    x = _solve(referenced, bounds, equations, maximize=True)

    if x is not None:
        x_map = {c: float(x[i]) for i, c in enumerate(referenced)}
        # Independent verification of the returned witness.
        for c in referenced:
            f = facts_by_concept[c]
            if not (f["lower"] - config.WITNESS_TOL <= x_map[c]
                    <= f["upper"] + config.WITNESS_TOL):
                return _infeasible(referenced, bounds, equations, facts_by_concept)
        for eq in equations:
            if not math.isclose(_residual(eq, x_map), 0.0, abs_tol=config.WITNESS_TOL):
                return _infeasible(referenced, bounds, equations, facts_by_concept)

        all_rows = _witness_rows(referenced, x, facts_by_concept)
        # unreferenced facts keep their own admissible true values
        for f in facts:
            if f["concept"] not in set(referenced):
                all_rows.append({
                    "concept": f["concept"],
                    "reported_value": f["value"],
                    "decimals": f["decimals"],
                    "true_value": f["value"],
                    "lower": f["lower"],
                    "upper": f["upper"],
                })
        return {
            "status": "feasible",
            "witness": all_rows,
            "max_abs_residual": max(
                (abs(_residual(eq, x_map)) for eq in equations), default=0.0
            ),
        }

    return _infeasible(referenced, bounds, equations, facts_by_concept)


def _minimal_infeasible_groups(concepts, bounds, equations) -> list[list[dict]]:
    """Partition the equations into disjoint irreducibly infeasible groups.

    Each returned group stays infeasible, but dropping any single equation from
    it makes that group feasible — a tight set of mutually contradictory
    facts and relationships.
    """
    remaining = list(equations)
    groups: list[list[dict]] = []
    while _solve(concepts, bounds, remaining) is None:
        keep = list(remaining)
        for eq in list(remaining):
            if len(keep) == 1:
                break
            trial = [e for e in keep if e is not eq]
            if _solve(concepts, bounds, trial) is None:
                keep = trial
        groups.append(keep)
        remaining = [e for e in remaining if e not in keep]
        if not remaining:
            break
    return groups


def _infeasible(concepts, bounds, equations, facts_by_concept) -> dict:
    groups = _minimal_infeasible_groups(concepts, bounds, equations)

    # A best-effort witness for the conflict facts: interval midpoints, so the
    # response still shows concrete amounts and the residual of each equation.
    mid_x = np.array([(lo + hi) / 2.0 for lo, hi in bounds])
    x_map = {c: float(mid_x[i]) for i, c in enumerate(concepts)}
    rows = _witness_rows(concepts, mid_x, facts_by_concept)
    row_by_concept = {r["concept"]: r for r in rows}

    conflicts = []
    involved = set()
    for group in groups:
        for eq in group:
            involved.update(eq["coefficients"].keys())
            conflicts.append({
                "equation": eq,
                "residual_at_witness": round(_residual(eq, x_map), 10),
                "facts": [row_by_concept[c] for c in eq["coefficients"]],
            })
    return {
        "status": "infeasible",
        "conflicts": conflicts,
        "conflict_facts": [row_by_concept[c] for c in sorted(involved)],
    }
