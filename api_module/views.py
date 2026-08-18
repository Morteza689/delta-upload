"""Public API endpoints.

POST   /api/v1/files/           upload a file
GET    /api/v1/files/           list your files
GET    /api/v1/files/<slug>/    file details
PATCH  /api/v1/files/<slug>/    change publishing or price
DELETE /api/v1/files/<slug>/    delete
GET    /api/v1/me/              account and balance

Files uploaded through a key belong to that key's owner, so their revenue goes
to the same account.

Authentication, throttling, and the error shape are set once in settings and in
this module's helpers rather than per view, so a new endpoint cannot be added
without them by forgetting a decorator.
"""
import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from upload_module.models import UploadFile

from .exceptions import ApiError
from .pagination import ApiPagination
from .serializers import (
    AccountSerializer, FileCreateSerializer, FileSerializer,
    FileUpdateSerializer,
)
from .throttling import ReadThrottle, UploadThrottle, WriteThrottle

logger = logging.getLogger(__name__)


class FileCollectionView(APIView):
    """Upload a file, or list the ones this key owns."""

    throttle_classes = [UploadThrottle, ReadThrottle]

    def get(self, request):
        queryset = UploadFile.objects.filter(author_id=request.user.id)

        visibility = request.query_params.get('visibility')
        if visibility:
            queryset = queryset.filter(visibility=visibility)

        paginator = ApiPagination()
        page = paginator.paginate_queryset(
            queryset.order_by('-created_deta'), request, view=self,
        )
        serializer = FileSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        if 'file' not in request.FILES:
            # Distinct from a validation error: the usual cause is a JSON body
            # where multipart was needed, and saying so saves a support round.
            raise ApiError(
                'missing_file',
                _('فیلد file ارسال نشده است. درخواست باید multipart/form-data باشد.'),
            )

        serializer = FileCreateSerializer(data=request.data)
        if not serializer.is_valid():
            # A file over the cap is a 413, not a 422: it is the request that
            # is too large, and clients retry the two differently.
            if 'file' in serializer.errors:
                raise ApiError(
                    'file_too_large', _('حجم فایل بیشتر از حد مجاز است.'),
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    fields={'file': [str(e) for e in serializer.errors['file']]},
                )
            serializer.is_valid(raise_exception=True)

        upload = serializer.validated_data['file']
        self._check_quota(request.user, upload)

        upload_file = UploadFile(
            author=request.user,
            name=(serializer.validated_data.get('name') or upload.name)[:100],
            description=serializer.validated_data.get('description') or '',
            type_file=UploadFile.ChoiceType.File,
            # The slug must be unguessable: it is the only thing protecting the
            # file's public page.
            slug=get_random_string(12),
            file=upload,
            original_filename=upload.name[:255],
        )
        serializer.apply_to(upload_file)

        with transaction.atomic():
            upload_file.save()

        logger.info('api upload: file=%s user=%s key=%s',
                    upload_file.pk, request.user.pk, request.auth.prefix)
        return Response(
            FileSerializer(upload_file, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    def _check_quota(self, user, upload):
        """The quota applies to keys too.

        Without this an API key would be a way around the limit the web form
        enforces, and the cheapest storage on the site would be the one nobody
        is billed for.
        """
        from account_module import quota

        try:
            quota.check_upload(user, upload.size)
        except quota.QuotaExceeded as exc:
            raise ApiError(
                'quota_exceeded', str(exc),
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                fields={'quota': {
                    'used_bytes': exc.used,
                    'limit_bytes': exc.limit,
                    'incoming_bytes': exc.incoming,
                }},
            )


class FileDetailView(APIView):
    """Read, edit, or delete one file."""

    throttle_classes = [ReadThrottle, WriteThrottle]

    def get_object(self, request, slug):
        # Filtering on author keeps one site's key away from another's files,
        # and answers 404 rather than 403 so a key cannot be used to discover
        # which slugs exist.
        return get_object_or_404(
            UploadFile, slug=slug, author_id=request.user.id,
        )

    def get(self, request, slug):
        upload_file = self.get_object(request, slug)
        return Response(
            FileSerializer(upload_file, context={'request': request}).data
        )

    def patch(self, request, slug):
        upload_file = self.get_object(request, slug)
        serializer = FileUpdateSerializer(
            instance=upload_file, data=request.data, partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.apply_to(upload_file)
        upload_file.save()
        return Response(
            FileSerializer(upload_file, context={'request': request}).data
        )

    def delete(self, request, slug):
        # The model's delete also removes the file from storage, and the
        # Telegram signal clears the channel copy.
        self.get_object(request, slug).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AccountView(APIView):
    """The account behind the key."""

    throttle_classes = [ReadThrottle]

    def get(self, request):
        user = request.user
        files = UploadFile.objects.filter(author_id=user.id)

        return Response(AccountSerializer({
            'email': user.email,
            'username': user.username,
            'balance': user.balance,
            'files': files.count(),
            'total_downloads': sum(item.number_of_download for item in files),
            'api_key': request.auth,
        }).data)
