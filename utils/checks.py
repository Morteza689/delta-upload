"""Project system checks.

Run by manage.py check --deploy. They catch configurations that are quietly
dangerous on a real server.
"""
from django.conf import settings
from django.core.checks import Error, Warning, register

LOCMEM = 'django.core.cache.backends.locmem.LocMemCache'


def looks_like_production():
    """Is this actually running on a server?

    Django's test runner turns DEBUG off, so `not DEBUG` alone is not enough:
    without this guard every test run with the development key would stop at a
    SystemCheckError. The checks themselves are tested by calling them
    directly.
    """
    return not settings.DEBUG and not getattr(settings, 'TESTING', False)


@register('dup')
def check_shared_cache(app_configs, **kwargs):
    """An in-process cache in production means no working rate limit."""
    problems = []
    backend = settings.CACHES.get('default', {}).get('BACKEND', '')

    if looks_like_production() and backend == LOCMEM:
        problems.append(Warning(
            'کش پیش‌فرض LocMemCache است و بین worker‌ها مشترک نیست.',
            hint='REDIS_URL را ست کن. بدون آن هر worker شمارنده‌ی جداگانه دارد و '
                 'محدودیت واقعی چند برابر مقدار تنظیم‌شده می‌شود.',
            id='dup.W001',
        ))

    return problems


@register('dup')
def check_ratelimit_enabled(app_configs, **kwargs):
    if looks_like_production() and not getattr(settings, 'RATELIMIT_ENABLE', True):
        return [Warning(
            'RATELIMIT_ENABLE خاموش است؛ هیچ محدودیتی روی ورود و پرداخت اعمال نمی‌شود.',
            id='dup.W002',
        )]
    return []


@register('dup')
def check_secret_key(app_configs, **kwargs):
    """The development key on a real server lets anyone forge a session.

    Session signatures, password reset tokens and every signed message come
    from this key. A key that is in the repository means anyone who has seen
    the code can sign in as an administrator.
    """
    from DUploaderProject.settings import DEV_SECRET_KEY

    if looks_like_production() and settings.SECRET_KEY == DEV_SECRET_KEY:
        return [Error(
            'SECRET_KEY همان کلید پیش‌فرض توسعه است.',
            hint='DJANGO_SECRET_KEY را با یک مقدار تصادفی ست کن: '
                 'python -c "import secrets; print(secrets.token_urlsafe(64))"',
            id='dup.E002',
        )]
    return []


@register('dup')
def check_email_configured(app_configs, **kwargs):
    """Without SMTP, verification mail only reaches the log."""
    console = 'django.core.mail.backends.console.EmailBackend'

    if looks_like_production() and settings.EMAIL_BACKEND == console:
        return [Warning(
            'EMAIL_HOST_PASSWORD ست نشده و ایمیل‌ها فقط در لاگ چاپ می‌شوند.',
            hint='بدون آن ایمیل فعال‌سازی حساب و بازیابی رمز به دست کاربر نمی‌رسد '
                 'و ثبت‌نام عملاً کار نمی‌کند.',
            id='dup.W003',
        )]
    return []


@register('dup')
def check_payment_secrets(app_configs, **kwargs):
    """A gateway without a signing key has a forgeable webhook."""
    problems = []
    crypto = getattr(settings, 'PAYMENT_GATEWAYS', {}).get('crypto', {})

    if looks_like_production() and crypto.get('api_key') and not crypto.get('ipn_secret'):
        problems.append(Error(
            'درگاه کریپتو کلید API دارد ولی IPN secret ندارد.',
            hint='بدون NOWPAYMENTS_IPN_SECRET امضای webhook بررسی نمی‌شود و هر کسی '
                 'می‌تواند با یک POST ساده کیف پول خودش را شارژ کند.',
            id='dup.E001',
        ))

    return problems
