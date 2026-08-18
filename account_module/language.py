"""Remembering which language to write to someone in.

The site takes the language from a cookie, which is right for a page: it
follows whichever browser is in front of you, signed in or not. Mail has no
browser and no cookie, so the choice has to be recorded against the account as
well or every message goes out in the default language.

Recorded silently when a signed-in visitor switches, so nobody has to find a
preference form to be written to correctly.
"""
import logging

from django.conf import settings
from django.utils import translation
from django.views.i18n import set_language as django_set_language

logger = logging.getLogger(__name__)


def set_language(request):
    """Django's own view, plus a note on the account.

    Wrapping rather than replacing: the cookie, the redirect and the safety
    checks on `next` are Django's problem and it already solves them.
    """
    response = django_set_language(request)

    user = getattr(request, 'user', None)
    chosen = request.POST.get('language') or request.GET.get('language')

    if user is not None and user.is_authenticated and chosen:
        # Normalised through Django, so 'en-gb' is stored as the 'en' we
        # actually have a catalogue for. A code we do not carry raises, and is
        # simply not recorded: Django's own view has already ignored it, so
        # the account should not end up disagreeing with the cookie.
        try:
            code = translation.get_supported_language_variant(chosen, strict=False)
        except LookupError:
            logger.debug('ignored unsupported language %r', chosen)
            return response

        if code in dict(settings.LANGUAGES) and user.language != code:
            user.language = code
            user.save(update_fields=['language'])

    return response


def language_for(user):
    """The language to write to this user in.

    Falls back to the site default rather than to whatever happens to be
    active: mail is often sent from a management command, where the active
    language is nobody's choice.
    """
    code = getattr(user, 'language', '') or ''
    if code in dict(settings.LANGUAGES):
        return code
    return settings.LANGUAGE_CODE
