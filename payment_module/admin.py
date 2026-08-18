from django.contrib import admin

from . import models


@admin.register(models.ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ['id', 'usd_to_toman', 'is_active', 'created_at']
    list_filter = ['is_active']


@admin.register(models.Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'gateway', 'amount', 'status',
                    'tracking_code', 'created_at', 'paid_at']
    list_filter = ['gateway', 'status', 'created_at']
    search_fields = ['user__email', 'user__username', 'gateway_ref', 'tracking_code']
    # Financial history: editing a status by hand splits the balance from the
    # ledger.
    readonly_fields = ['user', 'gateway', 'amount', 'status', 'gateway_ref',
                       'tracking_code', 'rate_snapshot', 'transaction', 'error',
                       'created_at', 'paid_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
