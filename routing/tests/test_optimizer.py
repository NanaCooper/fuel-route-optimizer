import random
from functools import cache

from django.test import SimpleTestCase

from routing.services.optimizer import InfeasibleRouteError, plan_fuel_stops

INF = float('inf')


def brute_force(stations, total, rng, start_full, reserve, penalty):
    """Exhaustive search over integer fuel levels: ground truth for small instances.

    Returns the minimum of fuel cost + penalty x stops, under the same start rules as the planner.
    """
    pos = [s[0] for s in stations] + [total]
    price = [s[1] for s in stations]
    n = len(stations)

    @cache
    def best(i, fuel, stopped):
        # At station i (destination when i == n) with `fuel`; `stopped` = penalty already paid here.
        if i == n:
            return 0.0
        out = INF
        gap = pos[i + 1] - pos[i]
        for buy in range(rng - fuel + 1):
            have = fuel + buy
            if gap > have:
                continue
            extra = penalty if buy > 0 and not stopped else 0.0
            out = min(out, buy * price[i] + extra + best(i + 1, have - gap, False))
        return out

    if start_full:
        if total <= rng:
            return 0.0
        if not n or pos[0] > rng:
            return INF
        return best(0, rng - pos[0], False)

    candidates = [k for k in range(n) if pos[k] <= reserve] or ([0] if n and pos[0] <= rng else [])
    return min((pos[k] * price[k] + penalty + best(k, 0, True) for k in candidates), default=INF)


def objective(plan, penalty):
    return plan.cost + penalty * len(plan.stops)


class OptimizerTests(SimpleTestCase):
    def test_buys_just_enough_to_reach_cheaper_fuel(self):
        plan = plan_fuel_stops([(2, 3.0), (100, 2.0)], total_miles=300, range_miles=500)
        self.assertEqual([s.station for s in plan.stops], [0, 1])
        self.assertAlmostEqual(plan.stops[0].miles_of_fuel, 100)  # 2 origin miles + 98 to reach mile 100
        self.assertAlmostEqual(plan.stops[1].miles_of_fuel, 200)
        self.assertAlmostEqual(plan.cost, 100 * 3.0 + 200 * 2.0)

    def test_fills_up_at_cheap_station(self):
        plan = plan_fuel_stops([(0, 2.0), (400, 5.0), (600, 4.0)], total_miles=900, range_miles=500)
        self.assertEqual([(s.station, round(s.miles_of_fuel)) for s in plan.stops], [(0, 500), (1, 100), (2, 300)])

    def test_stop_penalty_avoids_stops_that_save_pennies(self):
        stations = [(0, 3.00), (100, 2.99), (200, 3.00)]
        pure = plan_fuel_stops(stations, total_miles=450, range_miles=500)
        practical = plan_fuel_stops(stations, total_miles=450, range_miles=500, stop_penalty=10)
        self.assertEqual(len(pure.stops), 2)  # saves 3.5 by stopping twice
        self.assertEqual(len(practical.stops), 1)  # not worth a second stop
        self.assertLess(pure.cost, practical.cost)

    def test_total_purchased_equals_trip_length_when_starting_empty(self):
        stations = [(1, 3.1), (180, 2.9), (420, 3.4), (650, 2.7), (900, 3.0), (1210, 3.3)]
        for penalty in (0, 25):
            plan = plan_fuel_stops(stations, total_miles=1500, range_miles=500, stop_penalty=penalty)
            self.assertAlmostEqual(sum(s.miles_of_fuel for s in plan.stops), 1500)

    def test_start_full_short_trip_needs_no_fuel(self):
        plan = plan_fuel_stops([(100, 3.0)], total_miles=450, range_miles=500, start_full=True)
        self.assertEqual((plan.stops, plan.cost), ([], 0))

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaisesRegex(InfeasibleRouteError, '600-mile stretch'):
            plan_fuel_stops([(0, 3.0), (600, 3.0)], total_miles=900, range_miles=500)

    def test_no_stations_is_infeasible(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops([], total_miles=100, range_miles=500)

    def test_matches_brute_force_on_random_instances(self):
        rnd = random.Random(7)
        feasible = 0
        for _ in range(600):
            rng = rnd.randint(4, 9)
            total = rnd.randint(5, 30)
            positions = sorted(rnd.sample(range(total), rnd.randint(1, min(total, 9))))
            stations = [(p, rnd.choice([1.0, 1.5, 2.0, 2.5, 3.0, 3.5])) for p in positions]
            start_full = rnd.random() < 0.3
            reserve = rnd.randint(0, 3)
            penalty = rnd.choice([0, 0, 1, 2.5, 6])
            expected = brute_force(stations, total, rng, start_full, reserve, penalty)
            try:
                got = objective(
                    plan_fuel_stops(
                        stations, total, rng, start_full=start_full, origin_reserve_miles=reserve, stop_penalty=penalty
                    ),
                    penalty,
                )
            except InfeasibleRouteError:
                got = INF
            self.assertAlmostEqual(
                got, expected, places=4, msg=f'{stations=} {total=} {rng=} {start_full=} {reserve=} {penalty=}'
            )
            feasible += expected != INF
        self.assertGreater(feasible, 200)

    def test_large_instance_is_fast(self):
        import time

        rnd = random.Random(1)
        stations = sorted((rnd.uniform(0, 2800), rnd.uniform(2.8, 4.2)) for _ in range(600))
        t0 = time.perf_counter()
        plan_fuel_stops(stations, total_miles=2800, range_miles=500, stop_penalty=50)
        self.assertLess(time.perf_counter() - t0, 1.0)
