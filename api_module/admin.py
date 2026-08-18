from django.contrib import admin

from .models import ApiKey


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'user', 'prefix', 'is_active',
                    'created_at', 'last_used_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'prefix', 'user__email', 'user__username']
    # The hash is read-only: changing it silently breaks the client's key.
    readonly_fields = ['user', 'prefix', 'key_hash', 'created_at',
                       'last_used_at', 'revoked_at']
    actions = ['revoke_keys']

    def has_add_permission(self, request):
        # Keys must come from ApiKey.generate so the raw value is shown once.
        # A hand-made row produces a key nobody can use.
        return False

    @admin.action(description='ابطال کلیدهای انتخاب‌شده')
    def revoke_keys(self, request, queryset):
        count = 0
        for key in queryset.filter(is_active=True):
            key.revoke()
            count += 1
        self.message_user(request, f'{count} کلید باطل شد')
