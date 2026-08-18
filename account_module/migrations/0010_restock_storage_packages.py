"""Replace the storage catalogue after the free allowance dropped to 1 GB.

The old three packages started at 20 GB, which is a large first step up from
1 GB. This retires them and puts the current STORAGE_PLANS on sale instead.

Retired rather than deleted: StoragePlan rows freeze their own size and price at
purchase time, so nothing anyone already bought is touched, but a package that
was never on sale here should not linger in the admin either.

Packages an operator added by hand are left alone. Only the rows this project
seeded are retired, matched by their exact size and price.
"""
from django.conf import settings
from django.db import migrations

GIGABYTE = 1024 ** 3

#: What migration 0009 could have created, by (bonus_bytes, price_cents).
SEEDED_BY_0009 = {
    (20 * GIGABYTE, 200),
    (100 * GIGABYTE, 800),
    (500 * GIGABYTE, 3000),
}

FALLBACK = [
    {'bytes': 10 * GIGABYTE, 'price_cents': 100, 'days': 30},
    {'bytes': 50 * GIGABYTE, 'price_cents': 300, 'days': 30},
    {'bytes': 200 * GIGABYTE, 'price_cents': 800, 'days': 30},
    {'bytes': 500 * GIGABYTE, 'price_cents': 1800, 'days': 30},
    {'bytes': 2048 * GIGABYTE, 'price_cents': 4500, 'days': 30},
]


def restock(apps, schema_editor):
    StoragePackage = apps.get_model('account_module', 'StoragePackage')

    for package in StoragePackage.objects.all():
        if (package.bonus_bytes, package.price_cents) in SEEDED_BY_0009:
            package.is_active = False
            package.save(update_fields=['is_active'])

    plans = getattr(settings, 'STORAGE_PLANS', None) or FALLBACK
    for order, plan in enumerate(plans):
        # get_or_create keeps this safe to run against a database where an
        # operator already added one of these by hand.
        package, created = StoragePackage.objects.get_or_create(
            bonus_bytes=plan['bytes'],
            price_cents=plan['price_cents'],
            defaults={'days': plan.get('days', 30), 'sort_order': order},
        )
        if not created and not package.is_active:
            package.is_active = True
            package.save(update_fields=['is_active'])


def unstock(apps, schema_editor):
    StoragePackage = apps.get_model('account_module', 'StoragePackage')
    plans = getattr(settings, 'STORAGE_PLANS', None) or FALLBACK
    for plan in plans:
        StoragePackage.objects.filter(
            bonus_bytes=plan['bytes'], price_cents=plan['price_cents'],
        ).delete()
    for bonus_bytes, price_cents in SEEDED_BY_0009:
        StoragePackage.objects.filter(
            bonus_bytes=bonus_bytes, price_cents=price_cents,
        ).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [('account_module', '0009_seed_storage_packages')]
    operations = [migrations.RunPython(restock, unstock)]
