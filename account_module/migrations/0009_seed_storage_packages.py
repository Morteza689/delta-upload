"""Move the storage catalogue out of settings and into the database.

The values come from STORAGE_PLANS so an existing install ends up with a
catalogue without anyone setting one by hand. Without that setting, the
fallback below is used.
"""
from django.conf import settings
from django.db import migrations

GIGABYTE = 1024 ** 3

FALLBACK = [
    {'bytes': 20 * GIGABYTE, 'price_cents': 200, 'days': 30},
    {'bytes': 100 * GIGABYTE, 'price_cents': 800, 'days': 30},
    {'bytes': 500 * GIGABYTE, 'price_cents': 3000, 'days': 30},
]


def seed(apps, schema_editor):
    StoragePackage = apps.get_model('account_module', 'StoragePackage')
    if StoragePackage.objects.exists():
        return

    plans = getattr(settings, 'STORAGE_PLANS', None) or FALLBACK
    for order, plan in enumerate(plans):
        StoragePackage.objects.create(
            bonus_bytes=plan['bytes'],
            price_cents=plan['price_cents'],
            days=plan.get('days', 30),
            sort_order=order,
        )


def unseed(apps, schema_editor):
    apps.get_model('account_module', 'StoragePackage').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [('account_module', '0008_storagepackage')]
    operations = [migrations.RunPython(seed, unseed)]
