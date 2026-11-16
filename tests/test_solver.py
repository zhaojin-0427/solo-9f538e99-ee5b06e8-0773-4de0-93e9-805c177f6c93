"""Solver-level checks of the closed-interval decimals semantics."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.solver import check_facts


def _fact(concept, value, decimals, lower, upper):
    return {"concept": concept, "value": value, "decimals": decimals,
            "lower": lower, "upper": upper}


def _eq(summation, items):
    coef = {summation: -1}
    for c, w in items:
        coef[c] = coef.get(c, 0) + w
    return {"id": "eq1", "role": "r", "summation": summation,
            "items": [{"concept": c, "weight": w} for c, w in items],
            "coefficients": coef, "concept_count": len(coef)}


def test_inf_is_exact_and_contradictory_when_unequal():
    # exact facts: 2 + 3 must equal 4 -> impossible
    facts = [
        _fact("t:A", 2, "INF", 2, 2),
        _fact("t:B", 3, "INF", 3, 3),
        _fact("t:S", 4, "INF", 4, 4),
    ]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "infeasible"
    assert res["conflicts"][0]["residual_at_witness"] == 1.0


def test_inf_equal_is_feasible():
    facts = [
        _fact("t:A", 2, "INF", 2, 2),
        _fact("t:B", 3, "INF", 3, 3),
        _fact("t:S", 5, "INF", 5, 5),
    ]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "feasible"


def test_closed_interval_boundary_feasible():
    # decimals=0 => closed +-0.5. A=10.5 at upper edge, B=20.5 at upper edge,
    # S=31 at upper edge satisfies 31 == 31 on the boundary.
    facts = [
        _fact("t:A", 10, 0, 9.5, 10.5),
        _fact("t:B", 20, 0, 19.5, 20.5),
        _fact("t:S", 31, 0, 30.5, 31.5),
    ]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "feasible"
    w = {x["concept"]: x["true_value"] for x in res["witness"]}
    assert abs(w["t:A"] - 10.5) < 1e-9
    assert abs(w["t:B"] - 20.5) < 1e-9
    assert abs(w["t:S"] - 31.0) < 1e-9


def test_negative_decimals_large_rounding():
    # decimals=-2 => +-50. Reported 100 and 200 must sum to reported 400:
    # max A+B = 400, min S = 350, feasible on boundary A=150, B=250, S=400.
    facts = [
        _fact("t:A", 100, -2, 50, 150),
        _fact("t:B", 200, -2, 150, 250),
        _fact("t:S", 400, -2, 350, 450),
    ]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "feasible"

    # shift S to 500: min S = 450 > max A+B = 400 -> infeasible
    facts[2] = _fact("t:S", 500, -2, 450, 550)
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "infeasible"


def test_negative_weight():
    # S = A - B with exact facts 10, 4, 6
    facts = [
        _fact("t:A", 10, "INF", 10, 10),
        _fact("t:B", 4, "INF", 4, 4),
        _fact("t:S", 6, "INF", 6, 6),
    ]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", -1)])])
    assert res["status"] == "feasible"


def test_two_independent_contradictions_both_reported():
    facts = [
        _fact("t:A", 1, "INF", 1, 1),
        _fact("t:B", 1, "INF", 1, 1),
        _fact("t:S", 9, "INF", 9, 9),
        _fact("t:C", 2, "INF", 2, 2),
        _fact("t:D", 2, "INF", 2, 2),
        _fact("t:T", 8, "INF", 8, 8),
    ]
    eqs = [
        _eq("t:S", [("t:A", 1), ("t:B", 1)]),
        _eq("t:T", [("t:C", 1), ("t:D", 1)]),
    ]
    res = check_facts(facts, eqs)
    assert res["status"] == "infeasible"
    assert len(res["conflicts"]) == 2
    assert {c["equation"]["summation"] for c in res["conflicts"]} == {"t:S", "t:T"}


def test_missing_fact_short_circuits_to_pending():
    facts = [_fact("t:A", 1, "INF", 1, 1)]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "pending"
    assert res["missing_facts"] == ["t:B", "t:S"]


def test_single_self_contradictory_equation():
    # exact facts 1 + 1 = 3: one equation, infeasible on its own
    facts = [
        _fact("t:A", 1, "INF", 1, 1),
        _fact("t:B", 1, "INF", 1, 1),
        _fact("t:S", 3, "INF", 3, 3),
    ]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "infeasible"
    assert len(res["conflicts"]) == 1
    assert abs(res["conflicts"][0]["residual_at_witness"]) == 1.0


def test_shared_true_value_across_equations():
    # A participates in two equations; one shared true value must satisfy both.
    facts = [
        _fact("t:A", 10, "INF", 10, 10),
        _fact("t:B", 4, "INF", 4, 4),
        _fact("t:S", 6, "INF", 6, 6),    # A - B = 6
        _fact("t:C", 3, "INF", 3, 3),
        _fact("t:T", 7, "INF", 7, 7),    # A - C = 7
    ]
    eqs = [
        _eq("t:S", [("t:A", 1), ("t:B", -1)]),
        _eq("t:T", [("t:A", 1), ("t:C", -1)]),
    ]
    res = check_facts(facts, eqs)
    assert res["status"] == "feasible"
    w = {x["concept"]: x["true_value"] for x in res["witness"]}
    assert w["t:A"] == 10


# --------------------------------------------------------------------------
# Amounts above 2**53 must not be merged or rounded.
# --------------------------------------------------------------------------
BIG = 9007199254740993      # 2**53 + 1; float(BIG) == float(BIG-1)
BIG_LOW = 9007199254740992  # 2**53


def test_distinct_inf_amounts_above_two_pow_53_are_not_equal():
    # 9007199254740993 == 9007199254740992 must be infeasible.
    facts = [
        _fact("t:A", BIG, "INF", BIG, BIG),
        _fact("t:B", BIG_LOW, "INF", BIG_LOW, BIG_LOW),
    ]
    res = check_facts(facts, [_eq("t:A", [("t:B", 1)])])
    assert res["status"] == "infeasible"
    conflict = res["conflicts"][0]
    assert conflict["residual_at_witness"] == -1
    # the witness amounts are reported exactly, not as a collapsed float
    reported = {f["concept"]: f["reported_value"] for f in conflict["facts"]}
    assert reported["t:A"] == BIG
    assert reported["t:B"] == BIG_LOW


def test_equal_inf_amounts_above_two_pow_53_are_feasible():
    facts = [
        _fact("t:A", BIG, "INF", BIG, BIG),
        _fact("t:B", BIG, "INF", BIG, BIG),
    ]
    res = check_facts(facts, [_eq("t:A", [("t:B", 1)])])
    assert res["status"] == "feasible"
    witness = {row["concept"]: row["true_value"] for row in res["witness"]}
    assert witness["t:A"] == BIG


def test_large_decimals_zero_uses_exact_half_unit_bounds():
    # Amounts above 2**53 with decimals=0: the true values are x ± 0.5.
    # A = B is feasible exactly when the closed intervals touch, which the
    # binary floats would collapse to a single point.
    facts = [
        _fact("t:A", BIG, 0, "9007199254740992.5", "9007199254740993.5"),
        _fact("t:B", BIG_LOW, 0, "9007199254740991.5", "9007199254740992.5"),
    ]
    res = check_facts(facts, [_eq("t:A", [("t:B", 1)])])
    assert res["status"] == "feasible"
    witness = {row["concept"]: row["true_value"] for row in res["witness"]}
    # the only common true value is the shared interval boundary
    assert witness["t:A"] == witness["t:B"] == "9007199254740992.5"

    # shifting B up by two makes the closed intervals disjoint
    facts[1] = _fact("t:B", BIG, 0, "9007199254740992.5", "9007199254740993.5")
    # ...which is still feasible: they share the upper boundary
    assert check_facts(facts, [_eq("t:A", [("t:B", 1)])])["status"] == "feasible"
    facts[1] = _fact(
        "t:B", BIG + 2, 0, "9007199254740993.5", "9007199254740994.5"
    )
    assert check_facts(facts, [_eq("t:A", [("t:B", 1)])])["status"] == "feasible"
    facts[1] = _fact(
        "t:B", BIG + 4, 0, "9007199254740994.5", "9007199254740995.5"
    )
    assert check_facts(facts, [_eq("t:A", [("t:B", 1)])])["status"] == "infeasible"


def test_feasible_witness_is_exactly_inside_original_intervals():
    # 1/3-like rational corners must be reported without pretending to be floats.
    facts = [
        _fact("t:A", 1, 0, "0.5", "1.5"),
        _fact("t:B", 1, 0, "0.5", "1.5"),
        _fact("t:S", 2, 0, "1.5", "2.5"),
    ]
    res = check_facts(facts, [_eq("t:S", [("t:A", 1), ("t:B", 1)])])
    assert res["status"] == "feasible"
    from fractions import Fraction

    from app.amounts import to_fraction
    assert all(
        to_fraction(row["lower"]) <= to_fraction(row["true_value"])
        <= to_fraction(row["upper"])
        for row in res["witness"]
    )
    witness = {row["concept"]: to_fraction(row["true_value"])
               for row in res["witness"]}
    assert witness["t:S"] - witness["t:A"] - witness["t:B"] == 0
    assert res["max_abs_residual"] == 0

