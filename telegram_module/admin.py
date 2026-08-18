from django.contrib import admin

from .models import (
    TelegramChunk, TelegramConnection, TelegramFile, TelegramSetting,
)


@admin.register(TelegramSetting)
class TelegramSettingAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'is_enabled', 'updated_at']
    readonly_fields = ['updated_at']

    def has_add_permission(self, request):
        # One row. A second would be edited by someone expecting it to apply.
        return not TelegramSetting.objects.exists()


@admin.register(TelegramConnection)
class TelegramConnectionAdmin(admin.ModelAdmin):
    list_display = ['user', 'status', 'bot_username', 'chat_title', 'linked_at']
    list_filter = ['status']
    search_fields = ['user__email', 'chat_title', 'chat_id', 'bot_username']
    # Neither the token nor the link code is editable or readable here. The
    # token is the user's credential and the code lets whoever holds it attach
    # a channel to this account; staff need neither to do their job.
    exclude = ['bot_token_encrypted']
    readonly_fields = ['link_code', 'code_expires_at', 'linked_at', 'bot_id',
                       'bot_username', 'last_error', 'last_error_at',
                       'created_at']


class TelegramChunkInline(admin.TabularInline):
    model = TelegramChunk
    extra = 0
    readonly_fields = ['index', 'size_bytes', 'message_id', 'file_id',
                       'file_unique_id', 'created_at']
    can_delete = False

    def has_add_permission(self, request, obj):
        # Chunks describe messages that really exist. Inventing one here would
        # produce a file that fails halfway through a download.
        return False


@admin.register(TelegramFile)
class TelegramFileAdmin(admin.ModelAdmin):
    list_display = ['upload_file', 'status', 'total_bytes', 'attempts',
                    'created_at', 'stored_at']
    list_filter = ['status']
    search_fields = ['upload_file__name', 'connection__user__email']
    readonly_fields = ['upload_file', 'connection', 'total_bytes', 'chunk_bytes',
                       'created_at', 'stored_at', 'last_error']
    inlines = [TelegramChunkInline]
    actions = ['requeue']

    @admin.action(description='تلاش دوباره برای انتقال')
    def requeue(self, request, queryset):
        # Only rows that gave up. Resetting one mid-transfer would have two
        # workers writing the same chunks.
        updated = queryset.filter(status=TelegramFile.Status.FAILED).update(
            status=TelegramFile.Status.PENDING, attempts=0, last_error='',
        )
        self.message_user(request, f'{updated} فایل دوباره در صف قرار گرفت')
