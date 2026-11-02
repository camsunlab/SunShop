from django.contrib import admin

from .models import Attachment, Event, PurchaseRequest


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0


class EventInline(admin.TabularInline):
    model = Event
    extra = 0
    readonly_fields = ("actor", "action", "message", "created_at")


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "item_name", "requester", "total", "tier", "status", "created_at")
    list_filter = ("status", "tier", "method")
    search_fields = ("item_name", "vendor", "grant_code", "project", "order_reference")
    readonly_fields = ("total", "tier")
    inlines = [AttachmentInline, EventInline]
