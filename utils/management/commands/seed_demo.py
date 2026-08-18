"""Sample data for development and manual testing.

Everything it creates carries a recognisable prefix so --flush can find
and remove it without touching real records.

The point is coverage of the awkward cases, not volume: a private file, an
expired one, a rejected session for every anti-fraud reason, a settled and
an unsettled withdrawal.
"""
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from account_module import wallet
from account_module.models import PayoutAccount, Transaction, User, Withdrawal
from ads_module.models import AdEarning, AdSetting, AdSlot, DownloadSession, EarningCarry
from contact_module.models import ContactUs
from payment_module.models import ExchangeRate, Payment
from site_module.models import FooterLink, FooterLinkBox, SiteSetting
from upload_module import purchases
from upload_module.models import DownloadLink, Purchase, UploadFile

DEMO_PREFIX = 'demo_'
DEMO_PASSWORD = 'demo1234'


class Command(BaseCommand):
    help = 'دیتای نمونه برای توسعه می‌سازد'

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true',
                            help='قبل از ساختن، محتوای موجود را پاک کن')
        parser.add_argument('--purge-media', action='store_true',
                            help='فایل‌های یتیم داخل پوشه مدیا را هم پاک کن')
        parser.add_argument('--force', action='store_true',
                            help='اجرا حتی وقتی DEBUG خاموش است')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError(
                'DEBUG خاموش است. این دستور دیتای ساختگی می‌سازد و روی سرور واقعی '
                'نباید اجرا شود. اگر مطمئنی، --force بده.'
            )

        if options['flush']:
            self.flush(purge_media=options['purge_media'])

        self.seed()

    # ------------------------------------------------------------------ flush

    def flush(self, purge_media=False):
        self.stdout.write(self.style.WARNING('--- پاک کردن ---'))

        # Files are deleted one at a time rather than with queryset.delete(),
        # because only the model's delete also removes the file from storage.
        removed_files = 0
        for upload in UploadFile.objects.all():
            name = upload.file.name if upload.file else '(بدون فایل)'
            upload.delete()
            self.stdout.write(f'  حذف فایل: {name}')
            removed_files += 1

        counts = {
            'DownloadLink': DownloadLink.objects.all().delete()[0],
            'Purchase': Purchase.objects.all().delete()[0],
            'AdEarning': AdEarning.objects.all().delete()[0],
            'DownloadSession': DownloadSession.objects.all().delete()[0],
            'EarningCarry': EarningCarry.objects.all().delete()[0],
            'Payment': Payment.objects.all().delete()[0],
            'Withdrawal': Withdrawal.objects.all().delete()[0],
            'PayoutAccount': PayoutAccount.objects.all().delete()[0],
            'Transaction': Transaction.objects.all().delete()[0],
            'ContactUs': ContactUs.objects.all().delete()[0],
            'FooterLink': FooterLink.objects.all().delete()[0],
            'FooterLinkBox': FooterLinkBox.objects.all().delete()[0],
            'SiteSetting': SiteSetting.objects.all().delete()[0],
        }
        for label, count in counts.items():
            if count:
                self.stdout.write(f'  حذف {label}: {count}')

        # Balances have to match the emptied ledger, or a number is left with no
        # transaction behind it.
        User.objects.update(balance=0)

        demo_users = User.objects.filter(
            username__startswith=DEMO_PREFIX, is_superuser=False,
        )
        deleted_users = demo_users.count()
        demo_users.delete()
        if deleted_users:
            self.stdout.write(f'  حذف کاربر دمو: {deleted_users}')

        if purge_media:
            self.purge_media()

        self.stdout.write(f'  {removed_files} رکورد فایل پاک شد')

    def purge_media(self):
        """Files left in the upload directory with no record pointing at them."""
        from django.core.files.storage import FileSystemStorage

        storage = FileSystemStorage(location=settings.MEDIA_ROOT)
        try:
            _dirs, files = storage.listdir('dl/files')
        except FileNotFoundError:
            return

        for name in files:
            path = f'dl/files/{name}'
            size = storage.size(path)
            storage.delete(path)
            self.stdout.write(f'  حذف فایل یتیم: {name} ({size:,} bytes)')

    # ------------------------------------------------------------------- seed

    @transaction.atomic
    def seed(self):
        self.stdout.write(self.style.WARNING('--- ساخت دیتای نمونه ---'))

        self.ensure_settings()

        ali = self.make_user('ali', 'علی', 'رضایی')
        sara = self.make_user('sara', 'سارا', 'محمدی')
        reza = self.make_user('reza', 'رضا', 'کریمی')
        self.stdout.write(f'  ۳ کاربر دمو (رمز همه: {DEMO_PASSWORD})')

        now = timezone.now()
        files = [
            self.make_file(ali, 'ویدیو آموزش جنگو', 'قسمت اول دوره', 'django-intro',
                           size_kb=850),
            self.make_file(ali, 'قالب سایت فروشگاهی', 'HTML و CSS آماده', 'shop-template',
                           size_kb=2400, price=499),
            self.make_file(ali, 'فاکتور شخصی', 'این فایل نباید عمومی باشد', 'my-invoice',
                           size_kb=120, visibility=UploadFile.Visibility.PRIVATE),
            self.make_file(ali, 'فایل موقت ۲ ساعته', 'لینک بعد از ۲ ساعت می‌بندد',
                           'temp-2h', size_kb=340, public_until=now + timedelta(hours=2)),
            self.make_file(ali, 'فایل منقضی‌شده', 'پنجره انتشارش تمام شده', 'expired-one',
                           size_kb=90, public_until=now - timedelta(hours=1)),
            self.make_file(sara, 'مجموعه آیکون', 'SVG رایگان', 'icon-pack', size_kb=1200),
            self.make_file(sara, 'کتاب الکترونیکی', 'PDF آموزشی', 'ebook-python',
                           size_kb=5600, price=150),
        ]
        self.stdout.write(f'  {len(files)} فایل (رایگان، پولی، خصوصی، موقت، منقضی)')

        # --- Wallet: top-ups, purchases, sales ---
        self.make_payment(reza, 5000, Payment.Status.PAID, 'zarinpal', 'RF-98211')
        self.make_payment(reza, 2000, Payment.Status.AWAITING_CONFIRMATION, 'crypto')
        self.make_payment(sara, 1000, Payment.Status.FAILED, 'zarinpal')
        self.stdout.write('  ۳ پرداخت (موفق، در انتظار، ناموفق)')

        # $4.99 from the first user.
        purchases.purchase_file(reza, files[1])   # $4.99 from Ali
        # $1.50 from the second.
        purchases.purchase_file(reza, files[6])   # $1.50 from Sara
        self.stdout.write('  ۲ خرید فایل، با کمیسیون سایت')

        # --- Ad revenue: confirmed and pending ---
        setting = AdSetting.current()
        micro = setting.micro_per_impression() * 2
        confirmed = self.make_earnings(ali, files[0], 40, micro, ready=True)
        pending = self.make_earnings(ali, files[1], 15, micro, ready=False)
        self.make_earnings(sara, files[5], 22, micro, ready=True)

        from ads_module.services import confirm_user_earnings
        for user in (ali, sara):
            confirm_user_earnings(user)
        self.stdout.write(f'  درآمد تبلیغات: {confirmed} تأییدشده، {pending} در انتظار')

        # --- Rejected sessions: one example per anti-fraud reason ---
        self.make_rejected_sessions(files[0])

        # --- Download links: valid, used, expired ---
        self.make_download_links(files[0])

        # --- Payout destinations and withdrawals ---
        tron = PayoutAccount.objects.create(
            user=ali, method=PayoutAccount.Method.USDT_TRC20,
            destination='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
        )
        PayoutAccount.objects.create(
            user=ali, method=PayoutAccount.Method.SHEBA,
            destination='IR062960000000100324200001',
            holder_name='علی محمدی',
        )
        PayoutAccount.objects.create(
            user=sara, method=PayoutAccount.Method.USDT_BEP20,
            destination='0x2170Ed0880ac9A755fd29B2688956BD959F933F8',
        )
        ali.refresh_from_db()
        if ali.balance > 0:
            amount = min(ali.balance, 100)
            Withdrawal.objects.create(user=ali, account=tron,
                                      withdrawal_amount=amount)
            wallet.debit(ali, amount, Transaction.Kind.WITHDRAWAL,
                         description='درخواست برداشت نمونه')
            # One settled withdrawal, for the history.
            Withdrawal.objects.create(
                user=ali, account=tron, withdrawal_amount=50, is_paid=True,
                deta_is_paid=timezone.now() - timedelta(days=5),
            )
            self.stdout.write(f'  ۲ برداشت (یکی در انتظار {amount} سنت، یکی پرداخت‌شده)')

        self.seed_contact_messages(reza, sara)
        self.seed_site_settings()

        self.report()

    def make_rejected_sessions(self, upload_file):
        """Sessions that were not counted, one per reason.

        Visible in the admin, and useful for seeing what the anti-fraud rules
        actually caught.
        """
        reasons = ['too_fast', 'too_slow', 'duplicate', 'self_view', 'visitor_mismatch']
        for index, reason in enumerate(reasons):
            DownloadSession.objects.create(
                file=upload_file,
                visitor_hash=f'rejected-{index}'.ljust(64, 'r'),
                step2_at=timezone.now() - timedelta(minutes=index * 7),
                counted=False, reject_reason=reason,
            )
        self.stdout.write(f'  {len(reasons)} نشست ردشده (هر دلیل ضدتقلب یک نمونه)')

    def make_download_links(self, upload_file):
        now = timezone.now()
        DownloadLink.objects.create(file=upload_file, expires_at=now + timedelta(minutes=15))
        DownloadLink.objects.create(file=upload_file, expires_at=now + timedelta(minutes=15),
                                    is_used=True)
        DownloadLink.objects.create(file=upload_file, expires_at=now - timedelta(hours=1))
        self.stdout.write('  ۳ لینک دانلود (معتبر، مصرف‌شده، منقضی)')

    def seed_contact_messages(self, *users):
        messages = [
            ('مشکل در دانلود', 'لینک دانلود بعد از چند ثانیه منقضی می‌شود.', False, None),
            ('سوال درباره برداشت', 'حداقل مبلغ برداشت چقدر است؟', True,
             'حداقل مبلغ برداشت ۱۰ دلار است.'),
            ('پیشنهاد قابلیت', 'امکان آپلود پوشه هم اضافه کنید.', False, None),
        ]
        for index, (title, body, is_read, response) in enumerate(messages):
            user = users[index % len(users)]
            ContactUs.objects.create(
                title=title, email=user.email, full_name=user.get_full_name(),
                message=body, is_read_by_admin=is_read, response=response,
            )
        self.stdout.write(f'  {len(messages)} پیام تماس با ما (خوانده‌شده و نشده)')

    def seed_site_settings(self):
        SiteSetting.objects.create(
            site_name='دی‌آپ', site_url='d-up.ir',
            phone='02100000000', email='info@d-up.ir',
            copy_right='تمامی حقوق برای دی‌آپ محفوظ است.',
            about_us_text='سرویس اشتراک‌گذاری فایل با امکان کسب درآمد از دانلود.',
            is_main_setting=True,
        )
        boxes = {
            'دسترسی سریع': [('صفحه اصلی', 'https://d-up.ir/'),
                            ('آپلود فایل', 'https://d-up.ir/user/upload/')],
            'پشتیبانی': [('تماس با ما', 'https://d-up.ir/support/contact-us/'),
                         ('قوانین', 'https://d-up.ir/terms/')],
        }
        for title, links in boxes.items():
            box = FooterLinkBox.objects.create(title=title)
            for link_title, url in links:
                FooterLink.objects.create(title=link_title, url=url, footer_link_box=box)
        self.stdout.write('  تنظیمات سایت + ۲ دسته لینک فوتر (۴ لینک)')

    # ---------------------------------------------------------------- helpers

    def ensure_settings(self):
        if not ExchangeRate.objects.exists():
            ExchangeRate.objects.create(usd_to_toman=95000)
            self.stdout.write('  نرخ ارز: ۹۵٬۰۰۰ تومان')
        if not AdSetting.objects.exists():
            # Yektanet's own units, at the low end of the published native
            # band. The old seed used a flat hundred cents per thousand
            # impressions, which promised about fifty times what those rates
            # actually pay.
            AdSetting.objects.create(
                cpc_toman=400, clicks_per_mille=10, cpm_cents=0,
                uploader_share_percent=60, confirm_after_days=7,
                min_dwell_seconds=3, max_dwell_seconds=600, dedup_hours=24,
            )
            self.stdout.write('  تنظیمات تبلیغات: ۴۰۰ تومان هر کلیک، سهم ۶۰٪')
        for position, network in (('download_step1', 'yektanet'),
                                  ('download_step2', 'tapsell')):
            if not AdSlot.objects.filter(position=position).exists():
                AdSlot.objects.create(
                    position=position, network=network,
                    script=f'<div style="padding:1rem;color:#64748b;font-size:.8rem">'
                           f'[{network} placeholder - {position}]</div>',
                )

    def make_user(self, name, first, last):
        user = User.objects.create_user(
            username=f'{DEMO_PREFIX}{name}',
            email=f'{name}@demo.local',
            first_name=first, last_name=last,
            is_active=True, email_active_code=f'{name}-demo-code',
        )
        user.set_password(DEMO_PASSWORD)
        user.save()
        return user

    def make_file(self, author, name, description, slug, size_kb,
                  price=0, visibility=UploadFile.Visibility.PUBLIC,
                  public_until=None):
        # Real content, so sizes and downloads behave.
        body = (f'{name}\n'.encode() * 8).ljust(size_kb * 1024, b'.')
        upload = UploadFile(
            name=name, description=description, author=author, slug=slug,
            price=price, visibility=visibility, public_until=public_until,
            type_file=UploadFile.ChoiceType.File,
        )
        upload.file.save(f'{slug}.bin', ContentFile(body), save=False)
        upload.save()
        return upload

    def make_payment(self, user, amount, status, gateway, tracking=''):
        payment = Payment.objects.create(
            user=user, gateway=gateway, amount=amount, status=status,
            tracking_code=tracking, gateway_ref=f'REF-{user.pk}-{amount}',
        )
        if status == Payment.Status.PAID:
            payment.paid_at = timezone.now()
            entry = wallet.credit(user, amount, Transaction.Kind.TOPUP,
                                  description=f'شارژ کیف پول از {gateway}')
            payment.transaction = entry
            payment.save(update_fields=['paid_at', 'transaction'])
        return payment

    def make_earnings(self, user, upload_file, count, micro, ready):
        offset = -1 if ready else 3
        for index in range(count):
            session = DownloadSession.objects.create(
                file=upload_file, visitor_hash=f'{upload_file.pk}-{index}'.ljust(64, 'x'),
                counted=True, step2_at=timezone.now(),
            )
            AdEarning.objects.create(
                user=user, file=upload_file, session=session, micro_cents=micro,
                available_at=timezone.now() + timedelta(days=offset),
            )
        return count

    def report(self):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('--- خلاصه ---'))
        for user in User.objects.filter(username__startswith=DEMO_PREFIX):
            self.stdout.write(
                f'  {user.username:12s} موجودی={user.balance:6d} سنت  '
                f'فایل={user.uploadfile_set.count()}  '
                f'تراکنش={user.transactions.count()}'
            )
        self.stdout.write(f'  رمز عبور همه: {DEMO_PASSWORD}')
