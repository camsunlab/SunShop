from django.db import models
from django.contrib.auth.models import User
# Create your models here.
"""
Everytime you make changes to the models.py file, you need to run the following commands into terminal:
python manage.py makemigrations
python manage.py migrate

"""
class TodoItem(models.Model):
    title = models.CharField(max_length=100)
    completed = models.BooleanField(default=False)

class PurchaseRequest(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Ordered', 'Ordered'),
        ('Received', 'Received'),
        ('Cancelled', 'Cancelled'),
    ]
    # Requesting
    date_requested = models.DateField(auto_now_add=True)
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE)
    item_name = models.CharField(max_length=200)
    vendor = models.CharField(max_length=200)
    quantity = models.IntegerField(default=0)
    price = models.DecimalField(max_digits=15, decimal_places=2)
    purchase_link = models.URLField(blank=True)
    worktag = models.CharField(max_length=20, blank=True)
    project = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    # Ordering: Unified tracking fields 
    purchaser = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchased_items')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    ordered_date = models.DateField(blank=True, null=True)
    purchasepath = models.CharField(max_length=100, blank=True, default="")

    # Receiving
    received_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_by')
    received_date = models.DateField(blank=True, null=True)
    packing_slip = models.FileField(upload_to='packing_slips/', blank=True, null=True)

    def __str__(self):
        return f"{self.item_name} - {self.status} - {self.requested_by}"


# class PurchaseRequest(models.Model):
#     date_requested = models.DateField(auto_now_add=True)
#     requested_by = models.ForeignKey(User, on_delete=models.CASCADE)
#     item_name = models.CharField(max_length=200)
#     vendor = models.CharField(max_length=200)
#     quantity = models.IntegerField(default=0)
#     price = models.DecimalField(max_digits=10, decimal_places=2)
#     purchase_link = models.URLField(blank=True)
#     project = models.CharField(max_length=200, blank=True)
#     notes = models.TextField(blank=True)

#     def __str__(self):
#         #return self.item_name
#         return (f"{self.item_name} - {self.date_requested} - {self.requested_by}")

# class PurchaseOrder(models.Model):
#     STATUS_CHOICES = [
#         ('pending', 'Pending'),
#         ('ordered', 'Ordered'),
#     ]
#     purchase_request = models.OneToOneField(PurchaseRequest, on_delete=models.CASCADE)  # models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE) <-- many received records can point to one request
#     status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
#     ordered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='ordered_by')
#     ordered_date = models.DateField(auto_now_add=True)
#     purchasepath = models.CharField(max_length=30, blank=True, default="")
#     def __str__(self):
#         #return self.item_name
#         return (f"{self.purchase_request.item_name} - {self.ordered_date} - {self.status}")

# class PurchaseReceived(models.Model):
#     purchase_request = models.OneToOneField(PurchaseRequest, on_delete=models.CASCADE)  # models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE) <-- many received records can point to one request
#     is_received = models.BooleanField(default=False)
#     received_date = models.DateField(auto_now_add=True)
#     received_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_by')
#     packing_slip = models.FileField(upload_to='packing_slips/', blank=True, null=True)

#     def __str__(self):
#         #return self.item_name
#         return (f"{self.purchase_request.item_name} - {self.received_date} - {self.is_received}")