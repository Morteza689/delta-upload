"""Sending mail.

Every message goes out as HTML with a plain text alternative. The text part is
written by hand rather than stripped from the tags: a stripped version of a
table-based email arrives as a wall of run-together words, and the text part is
what spam filters read and what a text-only client shows.

Failures are logged and reported, never swallowed. This used to end in a bare
except that returned nothing, so an SMTP password typo looked exactly like a
delivered message and account activation silently stopped working.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


def site_context():
    """Values every message needs: the site's name and its address.

    Read once per send rather than passed in by each caller, so a template
    cannot end up with a blank header because somebody forgot a key.
    """
    base_url = getattr(settings, 'SITE_BASE_URL', '').rstrip('/')

    # The translated brand, not the one typed into site settings.
    #
    # That field holds a single string, and it is Persian. Dropped into an
    # English sentence it reads "Activate your دی‌آپ account". A per-language
    # name would need a second field; until there is one, the translation is
    # the only version that is right in both.
    name = _('دلتا آپلود')

    return {
        'site_name': name,
        'site_url': base_url,
        'support_email': settings.DEFAULT_FROM_EMAIL,
        # Passed explicitly because render_to_string without a request runs no
        # context processors, so the i18n ones are not there to supply them.
        'LANGUAGE_CODE': translation.get_language() or settings.LANGUAGE_CODE,
        'LANGUAGE_BIDI': translation.get_language_bidi(),
    }


def send_email(subject, to, context, template_name, text_template=None,
               language=None):
    """Render and send one message. Returns True when it was accepted.

    text_template defaults to the html name with a .txt suffix, so a template
    pair lives side by side and neither can be forgotten quietly.

    language overrides the active one for the whole message, subject included.
    Mail is often sent from a management command, where the active language is
    nobody's choice, and from a request, where it belongs to whoever is
    browsing rather than to the person being written to.
    """
    with translation.override(language or settings.LANGUAGE_CODE):
        return _send(subject, to, context, template_name, text_template)


def _send(subject, to, context, template_name, text_template):
    payload = {**site_context(), **(context or {})}

    try:
        html_body = render_to_string(template_name, payload)
    except TemplateDoesNotExist:
        logger.exception('email template missing: %s', template_name)
        return False

    text_name = text_template or template_name.rsplit('.', 1)[0] + '.txt'
    try:
        text_body = render_to_string(text_name, payload)
    except TemplateDoesNotExist:
        # Better a rough text part than none: a message with no text
        # alternative scores worse with spam filters.
        from django.utils.html import strip_tags

        logger.warning('no text template for %s; falling back to strip_tags',
                       template_name)
        text_body = strip_tags(html_body)

    message = EmailMultiAlternatives(
        subject=str(subject),
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    message.attach_alternative(html_body, 'text/html')

    try:
        sent = message.send(fail_silently=False)
    except Exception:
        # Caught, because a mail server being down should not turn a
        # registration into a 500. Logged, because the alternative is finding
        # out weeks later that nobody could activate an account.
        logger.exception('could not send %r to %s', subject, to)
        return False

    if not sent:
        logger.error('mail backend accepted nothing for %r to %s', subject, to)
        return False
    return True


def absolute_url(path):
    """A full URL for a link in an email.

    Built from SITE_BASE_URL rather than the request: mail is often sent from
    a management command, and a relative link in an email goes nowhere.
    """
    base = getattr(settings, 'SITE_BASE_URL', '').rstrip('/')
    return f"{base}/{path.lstrip('/')}"
