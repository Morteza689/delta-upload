"""Request and response shapes for the public API.

The output shape is a contract with sites already calling this, so it is
written out field by field rather than generated from the model. A field added
to UploadFile must not appear in the API by accident, and one renamed must not
silently disappear from it.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers

from upload_module.models import UploadFile


class FileSerializer(serializers.Serializer):
    """What a file looks like coming out of the API."""

    slug = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    size = serializers.SerializerMethodField()
    visibility = serializers.CharField(read_only=True)
    price = serializers.IntegerField(read_only=True)
    is_free = serializers.BooleanField(read_only=True)
    public_until = serializers.DateTimeField(read_only=True)
    is_public_now = serializers.BooleanField(read_only=True)
    downloads = serializers.IntegerField(
        source='number_of_download', read_only=True,
    )
    created_at = serializers.DateTimeField(source='created_deta', read_only=True)
    download_page_url = serializers.SerializerMethodField()

    def get_size(self, upload_file):
        return upload_file.size_bytes

    def get_download_page_url(self, upload_file):
        """The download page, not the file itself.

        That page is where the ad renders and where the revenue is counted, so
        handing out a direct link instead would cost the uploader money.
        """
        request = self.context.get('request')
        if not upload_file.slug or request is None:
            return None
        return request.build_absolute_uri(upload_file.get_absolute_url())


class PublishingSerializer(serializers.Serializer):
    """The publishing and pricing fields, shared by create and update."""

    name = serializers.CharField(max_length=100, required=False,
                                 allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    visibility = serializers.ChoiceField(
        choices=UploadFile.Visibility.choices, required=False,
    )
    price = serializers.IntegerField(required=False, min_value=0)
    # A duration in, a moment in time out. Clients should not have to reason
    # about our clock or our timezone to publish something for a day.
    public_for_hours = serializers.IntegerField(
        required=False, min_value=1, allow_null=True,
    )

    def validate(self, attrs):
        visibility = attrs.get('visibility') or self._current_visibility()
        price = attrs.get('price', self._current_price())

        if visibility == UploadFile.Visibility.PRIVATE and price:
            raise serializers.ValidationError(
                {'price': [_('فایل خصوصی قابل فروش نیست.')]}
            )
        return attrs

    def _current_visibility(self):
        instance = self.instance
        return instance.visibility if instance else UploadFile.Visibility.PRIVATE

    def _current_price(self):
        return self.instance.price if self.instance else 0

    def apply_to(self, upload_file):
        """Write the validated fields onto a file."""
        data = self.validated_data

        if 'name' in data:
            upload_file.name = data['name'][:100]
        if 'description' in data:
            upload_file.description = data['description']
        if 'visibility' in data:
            upload_file.visibility = data['visibility']
        if 'price' in data:
            upload_file.price = data['price']

        if 'public_for_hours' in data:
            hours = data['public_for_hours']
            upload_file.public_until = (
                timezone.now() + timedelta(hours=hours) if hours else None
            )
        return upload_file


class FileCreateSerializer(PublishingSerializer):
    """An upload. Everything but the file itself is optional."""

    file = serializers.FileField()

    def validate_file(self, upload):
        # Without a cap one key can fill the bucket.
        limit = getattr(settings, 'MAX_UPLOAD_SIZE_BYTES', 0)
        if limit and upload.size > limit:
            raise serializers.ValidationError(
                _('حداکثر %(limit)s بایت') % {'limit': limit}
            )
        return upload

    def validate(self, attrs):
        # A new file is private unless the caller says otherwise, so the
        # inherited check has the right default to work from.
        attrs.setdefault('visibility', UploadFile.Visibility.PRIVATE)
        return super().validate(attrs)


class FileUpdateSerializer(PublishingSerializer):
    """A partial edit. Absent fields are left alone."""


class ApiKeySerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    prefix = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class AccountSerializer(serializers.Serializer):
    """The caller's own account."""

    email = serializers.EmailField(read_only=True)
    username = serializers.CharField(read_only=True)
    # In cents, like every other amount in this system.
    balance_cents = serializers.IntegerField(source='balance', read_only=True)
    files = serializers.IntegerField(read_only=True)
    total_downloads = serializers.IntegerField(read_only=True)
    api_key = ApiKeySerializer(read_only=True)
