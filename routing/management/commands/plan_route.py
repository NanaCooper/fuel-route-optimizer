from django.core.management.base import BaseCommand, CommandError

from routing.exceptions import PlannerError
from routing.services.planner import plan_trip


class Command(BaseCommand):
    help = 'Plan a trip from the command line, e.g. manage.py plan_route "Chicago, IL" "Denver, CO"'

    def add_arguments(self, parser):
        parser.add_argument('start')
        parser.add_argument('finish')
        parser.add_argument('--start-full', action='store_true')
        parser.add_argument('--stop-cost', type=float)

    def handle(self, *args, **opts):
        try:
            plan = plan_trip(opts['start'], opts['finish'], start_full=opts['start_full'], stop_cost=opts['stop_cost'])
        except PlannerError as exc:
            raise CommandError(str(exc.detail)) from exc

        route, summary, meta = plan['route'], plan['fuel_summary'], plan['meta']
        self.stdout.write(
            f'{plan["start"]["label"]} -> {plan["finish"]["label"]}: '
            f'{route["distance_miles"]} mi, {route["duration_hours"]} h'
        )
        for s in plan['fuel_stops']:
            self.stdout.write(
                f'  {s["stop_number"]}. mile {s["mile_marker"]:>7}  ${s["price_per_gallon"]:<6} '
                f'{s["gallons_purchased"]:>6} gal  ${s["cost"]:>7.2f}  '
                f'{s["name"]} ({s["city"]}, {s["state"]})'
            )
        self.stdout.write(
            self.style.SUCCESS(
                f'Total: {summary["total_gallons"]} gal, ${summary["total_fuel_cost"]:.2f} '
                f'({summary["number_of_stops"]} stops, avg ${summary["average_price_per_gallon"]}/gal)'
            )
        )
        self.stdout.write(
            f'{meta["stations_in_corridor"]} stations in corridor, '
            f'{meta["external_api_calls"]} API call(s), {meta["response_time_ms"]} ms'
        )
