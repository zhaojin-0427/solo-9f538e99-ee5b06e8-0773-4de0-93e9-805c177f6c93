"""Exact rational linear feasibility / witness construction.

``scipy.optimize.linprog`` works in binary floating point.  That is fine for
small amounts but wrong for the actual task: two distinct ``decimals=INF``
amounts such as ``9007199254740993`` and ``9007199254740992`` collapse to the
same ``float``, and the half-unit closed intervals around large values are not
representable at all.  This module solves the feasibility problem directly over
:class:`fractions.Fraction` with a two-phase simplex, so every feasibility
decision and every witness is exact.

Problem solved (all equations share one true-value vector ``x``)::

    minimize  -sum(x_j)                     (deterministic corner witness)
    s.t.      A x = b                       (weighted summation equations)
              lower_j <= x_j <= upper_j     (closed decimals intervals)

The result is always a basic solution: each ``x_j`` is either fixed by the
bounds or an exact rational pivoted by the simplex, and it is verified against
the original equations before being returned.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Optional

from .errors import ApiError

_ZERO = Fraction(0)
_ONE = Fraction(1)
_DANTZIG_LIMIT = 4000


def _pivot(tableau: list[list[Fraction]], basis: list[int], row: int, col: int) -> None:
    pivot_row = tableau[row]
    pivot = pivot_row[col]
    if pivot != 1:
        pivot_row = [value / pivot for value in pivot_row]
        tableau[row] = pivot_row
    for i, other in enumerate(tableau):
        if i == row:
            continue
        factor = other[col]
        if factor:
            tableau[i] = [a - factor * b for a, b in zip(other, pivot_row)]
    basis[row] = col


def _simplex(tableau: list[list[Fraction]], basis: list[int],
             active: set[int]) -> bool:
    """Run primal simplex to optimality; return False when unbounded.

    The last row of ``tableau`` is the objective row (reduced costs plus the
    negated objective value) and is updated by every pivot, so each iteration
    costs one sweep rather than a full reduced-cost recomputation.  Entering
    columns follow Dantzig's rule for speed, falling back to Bland's rule after
    a long run to rule out cycling.
    """
    m = len(tableau) - 1  # last row is the objective row
    n = len(tableau[0]) - 1
    for iteration in range(200_000):
        # ``tableau[-1]`` is replaced by ``_pivot``, so read it every iteration.
        obj = tableau[-1]
        use_bland = iteration >= _DANTZIG_LIMIT
        entering = None
        if use_bland:
            for j in range(n):
                if j in active and j not in basis and obj[j] < 0:
                    entering = j
                    break
        else:
            best = _ZERO
            for j in range(n):
                if j in active and j not in basis and obj[j] < best:
                    best, entering = obj[j], j
        if entering is None:
            return True
        best_ratio: Optional[Fraction] = None
        leaving: Optional[int] = None
        for i in range(m):
            coefficient = tableau[i][entering]
            if coefficient > 0:
                ratio = tableau[i][n] / coefficient
                if best_ratio is None or ratio < best_ratio:
                    best_ratio, leaving = ratio, i
        if leaving is None:
            return False
        _pivot(tableau, basis, leaving, entering)
    raise ApiError(500, "solver_error", "exact simplex exceeded its pivot limit")


def _objective_row(tableau: list[list[Fraction]], basis: list[int],
                   costs: list[Fraction]) -> list[Fraction]:
    """Build the objective row ``[reduced costs..., -objective]``."""
    m = len(tableau)
    n = len(tableau[0]) - 1
    row = list(costs) + [_ZERO]
    for i, basic in enumerate(basis):
        coefficient = costs[basic]
        if coefficient:
            source = tableau[i]
            row = [a - coefficient * b for a, b in zip(row, source)]
    return row


def find_feasible(coefficients: list[dict[str, int]], rhs: list[Fraction],
                  lowers: list[Fraction], uppers: list[Fraction],
                  maximize: bool = True) -> Optional[list[Fraction]]:
    """Return an exact feasible ``x`` (optionally maximizing ``sum(x)``) or None."""
    n = len(lowers)
    if len(uppers) != n or len(rhs) != len(coefficients):
        raise ValueError("solver dimensions are inconsistent")
    # Coerce every input to Fraction: mixing ints into the tableau would make
    # ``int / int`` produce binary floats during pivoting.
    lowers = [Fraction(value) for value in lowers]
    uppers = [Fraction(value) for value in uppers]
    rhs = [Fraction(value) for value in rhs]
    coefficients = [
        {j: Fraction(coef) for j, coef in row.items()} for row in coefficients
    ]
    for lo, hi in zip(lowers, uppers):
        if lo > hi:
            raise ValueError("lower bound exceeds upper bound")

    m = len(coefficients)
    width = [hi - lo for lo, hi in zip(lowers, uppers)]

    # Shift x = lower + y with 0 <= y <= width.  ``b_shifted`` may be negative;
    # phase 1 introduces artificial variables and later drives them to zero.
    b_shifted: list[Fraction] = []
    for row, value in zip(coefficients, rhs):
        shifted = value
        for j, coef in row.items():
            shifted -= coef * lowers[j]
        b_shifted.append(shifted)

    # Columns: y (n), slack (n), artificial (m).  Rows: equations (m), then one
    # upper-bound row per variable: y_j + slack_j = width_j.
    artificial_base = 2 * n
    total_vars = 2 * n + m
    total_rows = m + n
    tableau = [[_ZERO] * (total_vars + 1) for _ in range(total_rows + 1)]
    basis: list[int] = [0] * total_rows

    for i, (row, b) in enumerate(zip(coefficients, b_shifted)):
        sign = -1 if b < 0 else 1
        for j, coef in row.items():
            tableau[i][j] = sign * coef
        tableau[i][artificial_base + i] = _ONE
        tableau[i][total_vars] = sign * b
        basis[i] = artificial_base + i

    for j in range(n):
        row = m + j
        tableau[row][j] = _ONE
        tableau[row][n + j] = _ONE
        tableau[row][total_vars] = width[j]
        basis[row] = n + j

    phase1_costs = [_ZERO] * total_vars
    for i in range(m):
        phase1_costs[artificial_base + i] = _ONE
    tableau[total_rows] = _objective_row(tableau, basis, phase1_costs)
    if not _simplex(tableau, basis, set(range(total_vars))):
        raise ApiError(500, "solver_error", "phase-1 LP reported unbounded")
    if tableau[total_rows][total_vars] != 0:
        return None

    # Drive any artificial variables that are still basic (at zero) out of the
    # basis before phase 2 so they can never re-enter.
    for i in range(total_rows):
        if basis[i] >= artificial_base:
            in_basis = set(basis)
            replacement = next(
                (j for j in range(2 * n)
                 if tableau[i][j] != 0 and j not in in_basis), None
            )
            if replacement is None:
                continue  # redundant row; phase 2 gives artificials zero cost
            _pivot(tableau, basis, i, replacement)
            tableau[total_rows] = _objective_row(tableau, basis, phase1_costs)

    phase2_costs = [(-_ONE if maximize else _ZERO)] * n + [_ZERO] * (total_vars - n)
    objective_row = _objective_row(tableau, basis, phase2_costs)
    tableau[total_rows] = objective_row
    if not _simplex(tableau, basis, set(range(2 * n))):
        raise ApiError(500, "solver_error", "phase-2 LP reported unbounded")

    solution = list(lowers)
    for i, basic_var in enumerate(basis):
        if basic_var < n:
            solution[basic_var] = lowers[basic_var] + tableau[i][total_vars]

    _verify(coefficients, rhs, lowers, uppers, solution)
    return solution


def _verify(coefficients, rhs, lowers, uppers, x) -> None:
    for j, (value, lo, hi) in enumerate(zip(x, lowers, uppers)):
        if not (lo <= value <= hi):
            raise ApiError(500, "solver_error",
                           f"exact solver witness left the interval for variable {j}")
    for row, value in zip(coefficients, rhs):
        total = sum(coef * x[j] for j, coef in row.items())
        if total != value:
            raise ApiError(500, "solver_error",
                           "exact solver witness does not satisfy an equation")
