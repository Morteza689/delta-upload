"""تبدیل Wallet به PayoutAccount.

روش‌های قدیمی (TRON بدون مشخص بودن ارز، و PYEER) حذف می‌شوند و جایشان را شبا و
تتر روی TRC20/BEP20 می‌گیرد.

مقصدهای قدیمی قابل تبدیل خودکار نیستند: آدرس ذخیره‌شده اعتبارسنجی نشده و
معلوم نیست به کدام شبکه تعلق دارد. پس پاک می‌شوند و کاربر مقصدش را دوباره ثبت
می‌کند — این از حدس زدن و واریز به آدرس اشتباه بی‌نهایت بهتر است.

برداشت‌های پرداخت‌نشده‌ای که مقصدشان پاک می‌شود هم حذف می‌شوند. مبلغشان قبلاً از
موجودی کم شده، پس همان‌جا به کاربر برگردانده می‌شود.
"""
import django.db.models.deletion
from django.db import migrations, models


def refund_and_clear(apps, schema_editor):
    """برداشت‌های در انتظار را لغو و مبلغشان را برمی‌گرداند."""
    Withdrawal = apps.get_model('account_module', 'Withdrawal')
    Transaction = apps.get_model('account_module', 'Transaction')
    User = apps.get_model('account_module', 'User')

    for withdrawal in Withdrawal.objects.filter(is_paid=False).select_related('user'):
        user = User.objects.get(pk=withdrawal.user_id)
        user.balance = (user.balance or 0) + withdrawal.withdrawal_amount
        user.save(update_fields=['balance'])

        Transaction.objects.create(
            user_id=user.pk,
            kind='topup',
            amount=withdrawal.withdrawal_amount,
            balance_after=user.balance,
            description='بازگشت وجه برداشت لغوشده (تغییر روش‌های برداشت)',
        )

    # برداشت‌های پرداخت‌شده هم می‌روند: مقصدشان دیگر وجود ندارد و رکورد بدون
    # مقصد قابل نگهداری نیست. تراکنش‌های دفتر کل سر جایشان می‌مانند.
    Withdrawal.objects.all().delete()


def noop(apps, schema_editor):
    """برگشت‌پذیر نیست: داده‌ی حذف‌شده را نمی‌شود بازساخت."""


class Migration(migrations.Migration):

    dependencies = [
        ('account_module', '0002_transaction'),
    ]

    operations = [
        migrations.RunPython(refund_and_clear, noop),

        migrations.RemoveField(model_name='withdrawal', name='wallet'),
        migrations.DeleteModel(name='Wallet'),

        migrations.CreateModel(
            name='PayoutAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('method', models.CharField(
                    choices=[
                        ('sheba', 'حساب بانکی — شبا'),
                        ('usdt-trc20', 'تتر (USDT) — شبکه TRC20'),
                        ('usdt-bep20', 'تتر (USDT) — شبکه BEP20'),
                    ],
                    max_length=30, verbose_name='روش برداشت')),
                ('destination', models.CharField(max_length=200,
                                                 verbose_name='شبا یا آدرس کیف پول')),
                ('holder_name', models.CharField(blank=True, max_length=120,
                                                 verbose_name='نام صاحب حساب')),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True,
                                                    verbose_name='تاریخ ثبت')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payout_accounts',
                    to='account_module.user', verbose_name='کاربر')),
            ],
            options={
                'verbose_name': 'مقصد برداشت',
                'verbose_name_plural': 'مقصدهای برداشت',
                'ordering': ['-created_at'],
            },
        ),

        migrations.AddConstraint(
            model_name='payoutaccount',
            constraint=models.UniqueConstraint(
                fields=('user', 'method', 'destination'),
                name='unique_payout_destination'),
        ),

        migrations.AddField(
            model_name='withdrawal',
            name='account',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to='account_module.payoutaccount',
                verbose_name='مقصد برداشت',
                # همه‌ی رکوردهای برداشت بالاتر پاک شده‌اند، پس ستون بدون
                # پیش‌فرض هم قابل افزودن است.
                default=None, null=False,
            ),
            preserve_default=False,
        ),
    ]
