"""Short link API.

GET    /api/v1/links/          list your links
POST   /api/v1/links/          create one
GET    /api/v1/links/<code>/   details
DELETE /api/v1/links/<code>/   delete

Authentication, throttling, and the error shape come from the project's REST
framework settings, the same ones the file API uses, so both halves of the
public API behave identically.
"""
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api_module.exceptions import ApiError
from api_module.pagination import ApiPagination
from api_module.throttling import ReadThrottle, WriteThrottle

from .models import ShortLink
from .serializers import ShortLinkCreateSerializer, ShortLinkSerializer


class LinkThrottle(WriteThrottle):
    """Creating a link is cheap, so it shares the write budget."""

    methods = ('POST', 'PATCH', 'PUT', 'DELETE')


class LinkCollectionView(APIView):
    throttle_classes = [ReadThrottle, LinkThrottle]

    def get(self, request):
        queryset = ShortLink.objects.filter(owner_id=request.user.id)
        paginator = ApiPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ShortLinkSerializer(
            page, many=True, context={'request': request},
        )
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        if 'target' not in request.data:
            raise ApiError('missing_target', _('فیلد target ارسال نشده است.'))

        serializer = ShortLinkCreateSerializer(
            data=request.data, context={'request': request},
        )
        if not serializer.is_valid():
            # A refused target has always had its own code rather than the
            # generic validation one, because it is the error clients actually
            # branch on: it means "we will not shorten that", not "you typed
            # the field name wrong".
            problems = [str(item) for item in serializer.errors.get('target', [])]
            raise ApiError(
                'invalid_target', problems[0] if problems else _('لینک نامعتبر است.'),
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                fields={'target': problems},
            )

        link = ShortLink.create_for(
            target=serializer.validated_data['target'], owner=request.user,
        )
        return Response(
            ShortLinkSerializer(link, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class LinkDetailView(APIView):
    throttle_classes = [ReadThrottle, LinkThrottle]

    def get_object(self, request, code):
        # Filtering on owner keeps one site's key away from another's links.
        return get_object_or_404(
            ShortLink, code=code, owner_id=request.user.id,
        )

    def get(self, request, code):
        return Response(
            ShortLinkSerializer(
                self.get_object(request, code), context={'request': request},
            ).data
        )

    def delete(self, request, code):
        self.get_object(request, code).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
