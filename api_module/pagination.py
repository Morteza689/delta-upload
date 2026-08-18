"""Limit and offset paging.

The response carries the limit and offset back rather than next and previous
URLs. Clients here paginate by arithmetic, and echoing what was applied lets
them see when a limit was clamped instead of silently getting fewer rows.
"""
from django.utils.translation import gettext as _
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response

from .exceptions import ApiError


class ApiPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100
    limit_query_param = 'limit'
    offset_query_param = 'offset'

    def paginate_queryset(self, queryset, request, view=None):
        # DRF ignores a limit or offset it cannot parse and quietly serves the
        # default page. A client that sent limit=abc is doing something wrong
        # and should be told, not handed twenty rows that look like an answer.
        for name in (self.limit_query_param, self.offset_query_param):
            raw = request.query_params.get(name)
            if raw is None:
                continue
            try:
                int(raw)
            except (TypeError, ValueError):
                raise ApiError(
                    'invalid_pagination',
                    _('limit و offset باید عدد صحیح باشند.'),
                )
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        return Response({
            'count': self.count,
            'limit': self.limit,
            'offset': self.offset,
            'results': data,
        })
