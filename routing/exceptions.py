from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class PlannerError(APIException):
    """Domain error rendered as {"error": {"code": ..., "message": ...}}."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = 'unprocessable'

    def __init__(self, message, status_code=None, code=None):
        super().__init__(detail=message, code=code or self.default_code)
        if status_code is not None:
            self.status_code = status_code
        self.code = code or self.default_code


class LocationError(PlannerError):
    default_code = 'invalid_location'


class UpstreamError(PlannerError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = 'routing_unavailable'


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, PlannerError):
        response.data = {'error': {'code': exc.code, 'message': str(exc.detail)}}
    elif response.status_code == status.HTTP_400_BAD_REQUEST:
        response.data = {'error': {'code': 'invalid_request', 'message': 'Invalid input.', 'fields': response.data}}
    return response
