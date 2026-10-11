from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import PlannerError
from .serializers import PlaceSearchSerializer, RouteRequestSerializer
from .services.places import search_places
from .services.planner import plan_trip
from .services.stations import get_index


def _map_url(request, data):
    query = {'start': data['start'], 'finish': data['finish']}
    if data.get('start_full'):
        query['start_full'] = 'true'
    if data.get('stop_cost') is not None:
        query['stop_cost'] = data['stop_cost']
    return request.build_absolute_uri(f'{reverse("route-map")}?{urlencode(query)}')


class RoutePlanView(APIView):
    """Plan a trip between two US locations with cost-optimal fuel stops.

    POST {"start": "Chicago, IL", "finish": "Denver, CO"}
    GET  /api/route/?start=Chicago, IL&finish=Denver, CO
    """

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, payload):
        serializer = RouteRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = plan_trip(**data)
        return Response({**result, 'map_url': _map_url(request, data)})


class PlaceSearchView(APIView):
    """City autocomplete from the offline US gazetteer (no external API calls).

    GET /api/places/?q=chi  ->  Chicago, IL; Chico, CA; ...  (most populous first)
    """

    def get(self, request):
        serializer = PlaceSearchSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        q, limit = serializer.validated_data['q'], serializer.validated_data['limit']
        response = Response({'query': q, 'results': search_places(q, limit)})
        response['Cache-Control'] = 'public, max-age=86400'  # the gazetteer is static
        return response


class HealthView(APIView):
    def get(self, request):
        return Response({'status': 'ok', 'stations_loaded': len(get_index())})


def route_map(request):
    """Interactive Leaflet map of a planned route. Served from the plan cache when possible."""
    serializer = RouteRequestSerializer(data=request.GET)
    context = {'form': request.GET}
    if 'start' in request.GET or 'finish' in request.GET:
        if serializer.is_valid():
            try:
                context['plan'] = plan_trip(**serializer.validated_data)
            except PlannerError as exc:
                context['error'] = str(exc.detail)
        else:
            context['error'] = 'Please enter both a start and a finish location.'
    response = render(request, 'routing/map.html', context)
    # OpenStreetMap's tile servers reject browser requests without a Referer ("403 Access blocked"
    # tiles). Django's default policy, same-origin, strips it; send only our origin instead.
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response
