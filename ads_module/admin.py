from django.contrib import admin
from django.utils.html import format_html

from . import models


@admin.register(models.AdSlot)
class AdSlotAdmin(admin.ModelAdmin):
    list_display = ['id', 'position', 'network', 'weight', 'is_active']
    list_filter = ['position', 'is_active', 'network']


@admin.register(models.AdSetting)
class AdSettingAdmin(admin.ModelAdmin):
    list_display = ['id', 'rate_summary', 'uploader_share_percent',
                    'confirm_after_days', 'is_active', 'updated_at']
    readonly_fields = ['payout_preview']
    fieldsets = (
        ('نرخ درآمد', {
            'fields': ('cpc_toman', 'clicks_per_mille', 'cpm_cents',
                       'uploader_share_percent', 'confirm_after_days',
                       'payout_preview'),
            'description': (
                'یکتانت به ازای <b>کلیک</b> پرداخت می‌کند، نه نمایش. ما نمایش '
                'می‌شماریم، چون کلیک سمت شبکه می‌افتد و هیچ‌وقت به ما گزارش '
                'نمی‌شود. پس نرخ هر نمایش از این دو عدد ساخته می‌شود.<br>'
                'هر دو را از پنل یکتانت بردار و بعد از هر گزارش ماهانه اصلاح کن.'
            ),
        }),
        ('وضعیت', {'fields': ('is_active',)}),
        ('ضدتقلب', {
            'fields': ('min_dwell_seconds', 'max_dwell_seconds', 'dedup_hours'),
            'description': 'کاربر بابت بازدید فایل خودش پول می‌گیرد، پس تقلب '
                           'بدیهی‌ترین کار ممکن است. این اعداد را شل نکنید.',
        }),
    )

    @admin.display(description='نرخ')
    def rate_summary(self, setting):
        if setting.cpm_cents:
            return f'{setting.cpm_cents}c/1000 (ثابت)'
        return f'{setting.cpc_toman:,} تومان/کلیک'

    @admin.display(description='این نرخ چقدر تعهد می‌سازد')
    def payout_preview(self, setting):
        """What the rate promises, in money, before it is saved.

        A cents-per-mille figure is impossible to sanity check by eye: the
        difference between a defensible number and one fifty times too high
        looks the same on the form. The same number as a payout for a hundred
        thousand downloads does not.
        """
        from payment_module.models import ExchangeRate

        if setting.pk is None:
            return 'بعد از ذخیره نشان داده می‌شود'

        rate = ExchangeRate.current()
        if rate is None:
            return format_html(
                '<b style="color:#b00">نرخ ارز ثبت نشده؛ تا وقتی ثبت نشود '
                'هیچ درآمدی محاسبه نمی‌شود.</b>'
            )

        cents_per_mille = setting.effective_cpm_cents()
        per_download = setting.micro_per_impression() * 2 / models.MICRO_PER_CENT

        return format_html(
            'هر ۱۰۰۰ نمایش: <b>{}</b> سنت<br>'
            'سهم آپلودکننده از یک دانلود: <b>${}</b><br>'
            '۱۰۰٬۰۰۰ دانلود: <b>${}</b><br>'
            '<span style="color:#666">نرخ ارز فعلی {} تومان.</span>',
            f'{cents_per_mille:.3f}',
            f'{per_download / 100:.6f}',
            f'{per_download * 100000 / 100:,.2f}',
            f'{rate.usd_to_toman:,}',
        )


@admin.register(models.DownloadSession)
class DownloadSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'file', 'step1_at', 'step2_at', 'counted', 'reject_reason']
    list_filter = ['counted', 'reject_reason', 'step1_at']
