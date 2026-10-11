from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200, trim_whitespace=True, help_text='"City, ST", "lat,lon" or a US street address'
    )
    finish = serializers.CharField(
        max_length=200, trim_whitespace=True, help_text='"City, ST", "lat,lon" or a US street address'
    )
    start_full = serializers.BooleanField(
        default=False, required=False, help_text='Vehicle leaves with a full tank (default: empty)'
    )
    stop_cost = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=1000,
        help_text='USD overhead per fuel stop used when optimising; 0 = cheapest fuel regardless of number of stops',
    )


class PlaceSearchSerializer(serializers.Serializer):
    q = serializers.CharField(
        max_length=100,
        trim_whitespace=True,
        help_text='What the user has typed so far, e.g. "chi" or "springfield, il"',
    )
    limit = serializers.IntegerField(default=8, min_value=1, max_value=20)
