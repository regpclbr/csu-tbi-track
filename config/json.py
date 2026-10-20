"""JSON responses for every API: date-times are shown in Manila time with the offset.

Django stores date-times in UTC and its JSON encoder writes them as "...Z". This encoder
converts them to the local time zone (settings.TIME_ZONE = Asia/Manila) first, so the API
shows e.g. "2026-10-26T18:00:00+08:00". Use `JsonResponse` from here in every view.
"""

from datetime import datetime

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse as DjangoJsonResponse
from django.utils import timezone


class LocalTimeJSONEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, datetime) and timezone.is_aware(value):
            value = timezone.localtime(value)
        return super().default(value)


class JsonResponse(DjangoJsonResponse):
    """Django's JsonResponse with Manila-time date-times."""

    def __init__(self, data, encoder=LocalTimeJSONEncoder, **kwargs):
        super().__init__(data, encoder=encoder, **kwargs)
