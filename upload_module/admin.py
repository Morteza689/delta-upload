from django.contrib import admin

from . import models


class UploadFileAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'author', 'visibility', 'price', 'public_until',
                    'number_of_download', 'is_active', 'created_deta']
    list_filter = ['visibility', 'is_active', 'created_deta']
    search_fields = ['name', 'slug', 'author__email']


class PurchaseAdmin(admin.ModelAdmin):
    list_display = ['id', 'buyer', 'file', 'amount', 'seller_share',
                    'site_commission', 'created_at']
    list_filter = ['created_at']
    search_fields = ['buyer__email', 'file__name']
    readonly_fields = ['buyer', 'file', 'amount', 'seller_share',
                       'site_commission', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(models.UploadFile, UploadFileAdmin)
admin.site.register(models.Purchase, PurchaseAdmin)
