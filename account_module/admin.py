from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from . import models


@admin.register(models.User)
class UserAdmin(BaseUserAdmin):
    """User admin.

    Must inherit Django's UserAdmin. With a plain ModelAdmin the password
    field becomes ordinary text and every save writes whatever is in it
    straight to the password column, unhashed.
    """

    list_display = ['id', 'username', 'email', 'balance',
                    'is_active', 'is_staff', 'date_joined']
    list_filter = ['is_active', 'is_staff', 'is_superuser', 'date_joined']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']

    # Balance is read-only here: every change must go through
    # account_module.wallet so the ledger records it, or the number and the
    # transaction history drift apart.
    readonly_fields = ['balance', 'last_login', 'date_joined']

    fieldsets = BaseUserAdmin.fieldsets + (
        ('دی‌آپ', {'fields': ('balance', 'email_active_code')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets


@admin.register(models.PayoutAccount)
class PayoutAccountAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'method', 'destination', 'holder_name', 'created_at']
    list_filter = ['method']
    search_fields = ['user__email', 'destination', 'holder_name']


@admin.register(models.Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'withdrawal_amount', 'account',
                    'is_paid', 'created_deta', 'deta_is_paid']
    list_filter = ['is_paid', 'created_deta']
    search_fields = ['user__email']
    # Amount, user and destination are fixed once requested; only the paid
    # flag is updated. Editing a destination would send the money elsewhere
    # without the user knowing.
    readonly_fields = ['user', 'withdrawal_amount', 'account', 'created_deta']


@admin.register(models.Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'kind', 'amount', 'balance_after', 'file', 'created_at']
    list_filter = ['kind', 'created_at']
    search_fields = ['user__email', 'user__username', 'description']
    # The ledger is financial history and must not be edited by hand.
    readonly_fields = ['user', 'kind', 'amount', 'balance_after', 'file',
                       'description', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(models.CaptchaSetting)
class CaptchaSettingAdmin(admin.ModelAdmin):
    list_display = ['provider', 'updated_at']

    def has_add_permission(self, request):
        # Single row; a second one only causes confusion.
        return not models.CaptchaSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Deleting would silently revert to the default.
        return False


@admin.register(models.StoragePackage)
class StoragePackageAdmin(admin.ModelAdmin):
    """Storage package catalogue.

    Edits here affect future sales only; existing plans froze their size and
    price at purchase time.
    """

    list_display = ['size_display', 'price_display', 'days', 'is_active',
                    'sort_order']
    list_editable = ['is_active', 'sort_order']
    list_filter = ['is_active']

    @admin.display(description='فضا')
    def size_display(self, obj):
        from account_module import quota
        return quota.human_size(obj.bonus_bytes)

    @admin.display(description='قیمت')
    def price_display(self, obj):
        return f'${obj.price_cents / 100:.2f}'


@admin.register(models.StoragePlan)
class StoragePlanAdmin(admin.ModelAdmin):
    """Purchased plans, read only.

    Hand editing means space the user did not pay for, or the reverse.
    """

    list_display = ['user', 'bonus_bytes', 'price_paid', 'created_at',
                    'expires_at']
    list_filter = ['created_at']
    search_fields = ['user__email']
    readonly_fields = ['user', 'bonus_bytes', 'price_paid', 'created_at']
