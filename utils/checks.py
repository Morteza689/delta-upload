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
            'نه EMAIL_API_KEY ست شده نه EMAIL_HOST_PASSWORD و ایمیل‌ها فقط در لاگ چاپ می‌شوند.',
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


@register('dup', deploy=True)
def check_static_files_served(app_configs, **kwargs):
    """With DEBUG off, something has to serve STATIC_URL.

    Registered as a deploy check, so it runs under `check --deploy` and not on
    every management command. Otherwise it makes a first deploy impossible:
    collectstatic has not run yet, so the check fails, so migrate refuses to
    run — and the only way out is to know which order defeats it.

    Django deliberately serves nothing there and assumes a web server in front
    is doing it. When nothing is, the site answers every request correctly and
    arrives with no stylesheet and no scripts, which reads as a broken deploy
    rather than a missing setting.
    """
    from pathlib import Path

    if not looks_like_production():
        return []

    if not settings.STATIC_ROOT:
        return [Error(
            'STATIC_ROOT ست نشده.',
            hint='بدون آن collectstatic جایی برای نوشتن ندارد و هیچ فایل '
                 'استاتیکی سرو نمی‌شود.',
            id='dup.E003',
        )]

    problems = []
    root = Path(settings.STATIC_ROOT)

    if not root.exists() or not any(root.iterdir()):
        problems.append(Warning(
            'STATIC_ROOT خالی است؛ collectstatic اجرا نشده.',
            hint='python manage.py collectstatic --noinput  — بدون این، CSS و '
                 'JS سایت لود نمی‌شوند.',
            id='dup.W004',
        ))

    backend = settings.STORAGES['staticfiles']['BACKEND']
    if 'Manifest' in backend and not (root / 'staticfiles.json').exists():
        problems.append(Error(
            'استوریج استاتیک manifest می‌خواهد ولی staticfiles.json نیست.',
            hint='هر {% static %} با خطا بالا می‌آید تا collectstatic اجرا شود.',
            id='dup.E004',
        ))

    if not any('whitenoise' in middleware for middleware in settings.MIDDLEWARE):
        problems.append(Warning(
            'WhiteNoise در MIDDLEWARE نیست.',
            hint='اگر nginx یا وب‌سروری جلوی جنگو فایل‌های STATIC_ROOT را سرو '
                 'می‌کند اشکالی ندارد. اگر نه — مثل دیپلوی سرورلس — هیچ CSS و '
                 'JS ای به مرورگر نمی‌رسد.',
            id='dup.W005',
        ))

    return problems
