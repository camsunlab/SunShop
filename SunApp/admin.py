from django.contrib import admin
from .models import TodoItem, PurchaseRequest

# Register your models here.
admin.site.register(TodoItem)
admin.site.register(PurchaseRequest)
