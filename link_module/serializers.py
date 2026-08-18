"""Short link shapes for the API.

Written out field by field for the same reason as the file serializer: a column
added to ShortLink must not appear in a public response by accident.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .forms import validate_target


class ShortLinkSerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    short_url = serializers.SerializerMethodField()
    target = serializers.CharField(read_only=True)
    clicks = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def get_short_url(self, link):
        request = self.context.get('request')
        if request is None:
            return None
        return request.build_absolute_uri(link.get_absolute_url())


class ShortLinkCreateSerializer(serializers.Serializer):
    target = serializers.CharField()

    def validate_target(self, value):
        target = value.strip()
        if not target:
            raise serializers.ValidationError('target is required')

        # The same check the web form uses. A shortener that will point at
        # anything is a shortener used to launder links away from filters, and
        # letting the API skip the check would make it the way in.
        try:
            validate_target(target, self.context.get('request'))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, 'messages', None) or [str(exc)]
            )
        return target
