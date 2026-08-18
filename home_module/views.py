from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

# The landing page is deliberately not login_required: its job is to bring
# new people in.


class HomeView(TemplateView):
    template_name = 'home_module/home_page.html'

    def get_context_data(self, **kwargs):
        from upload_module.guest import guest_size_limit

        context = super().get_context_data(**kwargs)
        # The cap is read from settings rather than hard-coded in the template, or a
        # change to MAX_GUEST_UPLOAD_SIZE_BYTES would have the landing page promising
        # a number the form rejects.
        context['guest_size_limit_mb'] = guest_size_limit() // (1024 * 1024)

        context['steps'] = [
            {
                'title': _('آپلود کن'),
                'body': _('فایلت را از پنل یا با API بفرست. تعیین کن عمومی باشد یا خصوصی، و اگر خواستی برایش قیمت بگذار.'),
            },
            {
                'title': _('لینک را پخش کن'),
                'body': _('یک صفحه‌ی دانلود می‌گیری که هر جا بخواهی می‌گذاری: کانال، سایت، شبکه اجتماعی.'),
            },
            {
                'title': _('درآمد بگیر'),
                'body': _('از هر دانلود سهم می‌گیری و هر وقت خواستی به کیف پول کریپتو برداشت می‌کنی.'),
            },
        ]

        context['features'] = [
            {
                'title': _('عمومی یا خصوصی'),
                'body': _('فایل خصوصی فقط در پنل خودت در دسترس است و هیچ لینک عمومی ندارد.'),
            },
            {
                'title': _('انتشار زمان‌دار'),
                'body': _('می‌توانی بگویی لینک فقط چند ساعت باز باشد؛ بعد از آن خودکار بسته می‌شود.'),
            },
            {
                'title': _('فروش فایل'),
                'body': _('برای فایلت قیمت بگذار. خریدار از کیف پولش پرداخت می‌کند و ۹۰٪ سهم تو است.'),
            },
            {
                'title': _('گزارش کامل'),
                'body': _('برای هر فایل ببین چند دانلود رایگان و چند دانلود پولی داشته و چقدر درآمد ساخته.'),
            },
        ]
        return context


class AboutView(TemplateView):
    template_name = 'home_module/about.html'


class TermsView(TemplateView):
    template_name = 'home_module/terms.html'


class PrivacyView(TemplateView):
    template_name = 'home_module/privacy.html'


class FaqView(TemplateView):
    template_name = 'home_module/faq.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['groups'] = [
            {
                'title': _('شروع کار'),
                'items': [
                    {
                        'q': _('چطور درآمد می‌گیرم؟'),
                        'a': _('هر بار که کسی از صفحه‌ی دانلود فایل تو عبور کند، سهمی از درآمد تبلیغات آن صفحه به حسابت می‌نشیند. اگر برای فایل قیمت گذاشته باشی، ۹۰٪ مبلغ فروش هم سهم توست.'),
                    },
                    {
                        'q': _('حداقل مبلغ برداشت چقدر است؟'),
                        'a': _('بستگی به روش دارد: واریز بانکی به شبا از ۲ دلار، و تتر روی شبکه‌ی TRC20 یا BEP20 از ۱۰ دلار. کارمزد انتقال از مبلغ برداشت کم می‌شود، یعنی مبلغی که دریافت می‌کنی به اندازه‌ی کارمزد کمتر از مبلغ درخواستی است. جدول دقیق حداقل و کارمزد هر روش در صفحه‌ی برداشت هست.'),
                    },
                    {
                        'q': _('حجم فایل تا چقدر می‌تواند باشد؟'),
                        'a': _('تا ۲ گیگابایت برای هر فایل. اگر به سقف بیشتری نیاز داری، تیکت بزن.'),
                    },
                ],
            },
            {
                'title': _('فایل و لینک'),
                'items': [
                    {
                        'q': _('تفاوت فایل عمومی و خصوصی چیست؟'),
                        'a': _('فایل عمومی صفحه‌ی دانلود دارد و هر کسی با داشتن لینک می‌تواند بگیردش. فایل خصوصی هیچ لینک عمومی ندارد و فقط از پنل خودت قابل دریافت است.'),
                    },
                    {
                        'q': _('می‌توانم لینک را بعد از مدتی ببندم؟'),
                        'a': _('بله. موقع آپلود می‌توانی بگویی فایل چند ساعت عمومی بماند؛ بعد از آن لینک خودکار بسته می‌شود و فایل در پنل خودت باقی می‌ماند.'),
                    },
                    {
                        'q': _('چرا صفحه‌ی دانلود دو مرحله دارد؟'),
                        'a': _('درآمد سایت و سهم تو از تبلیغات همین دو صفحه می‌آید. بدون آن، دانلود برای همه رایگان می‌شد ولی هیچ درآمدی هم ساخته نمی‌شد.'),
                    },
                ],
            },
            {
                'title': _('درآمد و پرداخت'),
                'items': [
                    {
                        'q': _('چرا درآمد تبلیغاتم بلافاصله قابل برداشت نیست؟'),
                        'a': _('درآمد چند روز در وضعیت «در انتظار تأیید» می‌ماند، چون خود شبکه‌ی تبلیغاتی هم ممکن است بعضی نمایش‌ها را نامعتبر اعلام کند. بعد از این مهلت، به موجودی قابل برداشت اضافه می‌شود.'),
                    },
                    {
                        'q': _('چرا بعضی بازدیدها شمرده نمی‌شوند؟'),
                        'a': _('بازدیدی که از ابزار خودکار بیاید، خیلی سریع رد شود، تکراری باشد، یا خودت باشی، درآمد نمی‌سازد. در گزارش هر فایل می‌توانی دلیل رد شدن هر مورد را ببینی.'),
                    },
                    {
                        'q': _('چطور کیف پولم را شارژ کنم؟'),
                        'a': _('از صفحه‌ی شارژ کیف پول، با درگاه بانکی یا ارز دیجیتال. شارژ برای خرید فایل‌های پولی دیگران لازم است.'),
                    },
                ],
            },
        ]
        return context


@require_GET
def robots_txt(request):
    """robots.txt.

    Panel and API paths are closed: they are user content and indexing them helps
    nobody.

    /s/ is closed for a different reason. Indexed short links make our shortener
    attractive to spammers, who would be borrowing the domain's reputation to lift
    their own targets in search results.
    """
    lines = [
        'User-agent: *',
        'Disallow: /user/',
        'Disallow: /admin/',
        'Disallow: /staff/',
        'Disallow: /api/',
        'Disallow: /api-keys/',
        'Disallow: /payment/',
        'Disallow: /account/files/',
        'Disallow: /s/',
        'Allow: /',
        '',
        f'Sitemap: {request.build_absolute_uri("/sitemap.xml")}',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain')


def error_404(request, exception=None):
    return render(request, '404.html', status=404)


def error_500(request):
    return render(request, '500.html', status=500)


def error_403(request, exception=None):
    return render(request, '403.html', status=403)


def csrf_failure(request, reason=''):
    """The page shown when a form's CSRF token is missing or stale.

    Django's own is a bare, unstyled page, and handler403 does not cover this:
    a CSRF failure never raises PermissionDenied, it goes through
    CSRF_FAILURE_VIEW instead.

    This is not a rare corner. A tab left open past the cookie's lifetime, a
    browser that dropped the cookie, or a back button after signing out all
    land here, and the fix is almost always to reload and submit again.
    """
    return render(request, '403.html', {
        'is_csrf_failure': True,
        # Kept out of the page body: the reason names internals and is useful
        # in logs, not to the person who just wants their form to submit.
        'csrf_reason': reason,
    }, status=403)
