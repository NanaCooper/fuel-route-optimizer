"""Cost-optimal refuelling along a fixed route.

Problem: stations sit at known miles along the route, each with a price. The
tank holds `range_miles` of fuel. Choose where to stop and how much to buy so
that fuel cost + `stop_penalty` per stop is minimised. The stop penalty models
the real cost of pulling off the highway (time, detour). Without it, the pure
optimum happily stops for 1 gallon to save a cent. With stop_penalty=0 this is
exactly the classic minimum fuel-cost solution.

Exact dynamic programme (Khuller, Malekian & Mestre, "To fill or not to fill:
the gas station problem", 2007). In an optimal plan, at every stop you either
  * fill the tank completely, or
  * buy just enough to reach the next stop (arriving there empty).
So the fuel on arrival at a stop is either 0 or R - d(prev, here), and
C(i, g) = min cost to finish from station i holding g fuel can be evaluated
with a few precomputed per-station tables: O(n * m log m) overall, where m is
the number of stations within one tank (a few ms for a cross-country trip).

Fuel is measured in miles of range, so costs come out in (price x miles).
Divide by MPG for dollars (the planner does this).
"""

from bisect import bisect_left
from dataclasses import dataclass, field
from itertools import pairwise

EPS = 1e-9
TIE_BREAK = 1e-7  # makes equal-cost plans prefer fewer stops when stop_penalty == 0
INF = float('inf')


class InfeasibleRouteError(Exception):
    """A gap between fuel stations (or to the destination) exceeds the vehicle range."""


@dataclass
class Stop:
    station: int  # index into the input `stations` sequence
    miles_of_fuel: float  # range purchased at this stop


@dataclass
class Plan:
    stops: list[Stop] = field(default_factory=list)
    cost: float = 0.0  # fuel cost only (price x miles), stop penalties excluded


class _Solver:
    def __init__(self, positions, prices, total, rng, penalty):
        self.p, self.c, self.D, self.R = positions, prices, total, rng
        self.P = penalty + TIE_BREAK
        n = len(positions)
        self.reach_end = [0] * n  # stations i+1 .. reach_end[i]-1 are within one tank of i
        self.fill = [INF] * n  # min over k of P + C(k, R - d_ik)
        self.fill_arg = [-1] * n
        self.dist = [None] * n  # distances i -> k for k in reach, increasing
        self.suffix = [None] * n  # suffix minima of d_ik * c_i + P + C(k, 0)
        self.suffix_arg = [None] * n
        self.c0 = [INF] * n  # C(i, 0)

        j = n
        for i in range(n - 1, -1, -1):
            while j > i + 1 and positions[j - 1] - positions[i] > rng + EPS:
                j -= 1
            self.reach_end[i] = j
            self._build(i)

    def _build(self, i):
        p, c, P, R = self.p, self.c, self.P, self.R
        ks = range(i + 1, self.reach_end[i])

        best, arg = INF, -1
        for k in ks:
            v = P + self.cost(k, R - (p[k] - p[i]))[0]
            if v < best:
                best, arg = v, k
        self.fill[i], self.fill_arg[i] = best, arg

        dist = [p[k] - p[i] for k in ks]
        suffix, suffix_arg = [INF] * (len(dist) + 1), [-1] * (len(dist) + 1)
        for t in range(len(dist) - 1, -1, -1):
            k = i + 1 + t
            v = dist[t] * c[i] + P + self.c0[k]
            if v < suffix[t + 1]:
                suffix[t], suffix_arg[t] = v, k
            else:
                suffix[t], suffix_arg[t] = suffix[t + 1], suffix_arg[t + 1]
        self.dist[i], self.suffix[i], self.suffix_arg[i] = dist, suffix, suffix_arg
        self.c0[i] = self.cost(i, 0.0)[0]

    def cost(self, i, g):
        """C(i, g) -> (cost, action, next_station); action in {'finish', 'fill', 'exact'}."""
        p, c, R, D = self.p, self.c, self.R, self.D
        best = (INF, None, -1)
        if D - p[i] <= R + EPS:
            best = (max(0.0, D - p[i] - g) * c[i], 'finish', -1)
        if self.fill[i] < INF:
            v = (R - g) * c[i] + self.fill[i]
            if v < best[0]:
                best = (v, 'fill', self.fill_arg[i])
        t = bisect_left(self.dist[i], g - EPS)
        if t < len(self.dist[i]):
            v = self.suffix[i][t] - g * c[i]
            if v < best[0]:
                best = (v, 'exact', self.suffix_arg[i][t])
        return best

    def walk(self, i, g):
        """Reconstruct purchases starting at station i with g fuel."""
        purchases = []
        while True:
            _, action, k = self.cost(i, g)
            if action == 'finish':
                purchases.append((i, max(0.0, self.D - self.p[i] - g)))
                return purchases
            d = self.p[k] - self.p[i]
            if action == 'fill':
                purchases.append((i, self.R - g))
                g = self.R - d
            else:  # exact
                purchases.append((i, d - g))
                g = 0.0
            i = k


def plan_fuel_stops(
    stations: list[tuple[float, float]],
    total_miles: float,
    range_miles: float,
    start_full: bool = False,
    origin_reserve_miles: float = 50.0,
    stop_penalty: float = 0.0,
) -> Plan:
    """Choose where and how much to refuel.

    stations: sequence of (mile_along_route, price), sorted by mile.
    stop_penalty: overhead per stop in the same units as price x miles (dollars x MPG).
    start_full=False: the vehicle leaves empty and first fills up within `origin_reserve_miles`
    of the origin; the fuel for those first miles is bought at that first stop, so every
    mile of the trip is paid for.
    """
    positions = [float(s[0]) for s in stations]
    prices = [float(s[1]) for s in stations]
    solver = _Solver(positions, prices, float(total_miles), float(range_miles), stop_penalty)

    best_cost, best_start = INF, None
    if start_full:
        if total_miles <= range_miles + EPS:
            return Plan()
        for k, pos in enumerate(positions):
            if pos > range_miles + EPS:
                break
            v = solver.P + solver.cost(k, range_miles - pos)[0]
            if v < best_cost:
                best_cost, best_start = v, (k, range_miles - pos, 0.0)
    else:
        candidates = [k for k, pos in enumerate(positions) if pos <= origin_reserve_miles + EPS]
        if not candidates and positions and positions[0] <= range_miles + EPS:
            candidates = [0]  # nothing near the origin: use the first station on the route
        for k in candidates:
            v = positions[k] * prices[k] + solver.P + solver.c0[k]
            if v < best_cost:
                best_cost, best_start = v, (k, 0.0, positions[k])

    if best_start is None or best_cost == INF:
        gap = _largest_gap(positions, total_miles, range_miles, start_full, origin_reserve_miles)
        raise InfeasibleRouteError(gap)

    k, g, origin_miles = best_start
    purchases = solver.walk(k, g)
    purchases[0] = (purchases[0][0], purchases[0][1] + origin_miles)
    stops = [Stop(i, m) for i, m in purchases if m > EPS]
    return Plan(stops=stops, cost=sum(prices[s.station] * s.miles_of_fuel for s in stops))


def _largest_gap(positions, total, rng, start_full, reserve):
    if not positions:
        return 'No fuel stations were found along this route.'
    if not start_full and positions[0] > max(reserve, rng):
        return f'No fuel station within {reserve:.0f} miles of the origin.'
    points = [0.0, *positions, total]
    gaps = [(b - a, a) for a, b in pairwise(points)]
    size, at = max(gaps)
    return (
        f'The route has a {size:.0f}-mile stretch without fuel stations (from mile {at:.0f}), '
        f'longer than the {rng:.0f}-mile vehicle range.'
    )
