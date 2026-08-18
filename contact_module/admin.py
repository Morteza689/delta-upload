from django.contrib import admin

from . import models


class TicketMessageInline(admin.TabularInline):
    model = models.TicketMessage
    extra = 0
    readonly_fields = ['author', 'body', 'attachment', 'is_staff_reply', 'created_at']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        # Replies must go through the ticket page so the status updates too.
        return False


@admin.register(models.Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['id', 'subject', 'user', 'department', 'priority',
                    'status', 'last_message_at']
    list_filter = ['status', 'department', 'priority', 'created_at']
    search_fields = ['subject', 'user__email', 'user__username']
    readonly_fields = ['user', 'created_at', 'last_message_at', 'closed_at']
    inlines = [TicketMessageInline]


@admin.register(models.ContactUs)
class ContactUsAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'email', 'is_read_by_admin', 'created_deta']
    list_filter = ['is_read_by_admin', 'created_deta']
    search_fields = ['title', 'email', 'full_name']
