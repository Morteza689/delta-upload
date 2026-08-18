"""Whether to show the feature in the navigation.

A switched-off feature is hidden rather than shown leading to a page that only
explains why it does not work.
"""
from . import feature


def telegram(request):
    return {'telegram_enabled': feature.is_enabled()}
