from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from .rules import get_tier, tier_for


class Status(models.TextChoices):
    AWAITING_PI = "awaiting_pi", "Awaiting PI approval"
    CHANGES_REQUESTED = "changes_requested", "Changes requested"
    AWAITING_DEREK = "awaiting_derek", "Awaiting Derek"
    TO_ORDER = "to_order", "Ready to order"
    ORDERED = "ordered", "Ordered"
    RECEIVED = "received", "Received"
    AWAITING_REIMBURSEMENT = "awaiting_reimbursement", "Awaiting reimbursement"
    REIMBURSED = "reimbursed", "Reimbursed"
    BOUGHT_AT_STORE = "bought_at_store", "Bought at store"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


OPEN_STATUSES = (
    Status.AWAITING_PI, Status.CHANGES_REQUESTED, Status.AWAITING_DEREK,
    Status.TO_ORDER, Status.ORDERED, Status.AWAITING_REIMBURSEMENT,
)

# How each status counts on the Spending page. Rejected / cancelled requests aren't counted.
SPENT_STATUSES = (Status.ORDERED, Status.RECEIVED, Status.AWAITING_REIMBURSEMENT,
                  Status.REIMBURSED, Status.BOUGHT_AT_STORE)
COMMITTED_STATUSES = (Status.TO_ORDER, Status.AWAITING_DEREK)  # approved, not yet bought
PENDING_STATUSES = (Status.AWAITING_PI, Status.CHANGES_REQUESTED)

STATUS_COLOURS = {
    Status.AWAITING_PI: "warning", Status.CHANGES_REQUESTED: "warning", Status.AWAITING_DEREK: "info",
    Status.TO_ORDER: "primary", Status.ORDERED: "primary", Status.AWAITING_REIMBURSEMENT: "primary",
    Status.RECEIVED: "success", Status.REIMBURSED: "success", Status.BOUGHT_AT_STORE: "success",
    Status.REJECTED: "danger", Status.CANCELLED: "secondary",
}


class Method(models.TextChoices):
    PURCHASE_FORM = "purchase_form", "Purchaser orders it (purchase form)"
    STORE = "store", "I bought it at the store with the grant code"
    REIMBURSEMENT = "reimbursement", "I paid for it myself – reimburse me"


class PurchaseRequest(models.Model):
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_requests")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # What
    item_name = models.CharField(max_length=200)
    spec = models.TextField(help_text="What it is, why it's needed, and any technical spec.")
    vendor = models.CharField(max_length=200)
    purchase_link = models.URLField(blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="£, including VAT")
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"), help_text="£, delivery / other charges")
    total = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    tier = models.PositiveSmallIntegerField(editable=False)

    # Funding
    grant_code = models.CharField(max_length=50)
    project = models.CharField(max_length=200, blank=True)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.PURCHASE_FORM)
    needed_by = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.AWAITING_PI)

    # PI approval
    pi_decision_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    pi_decision_at = models.DateTimeField(null=True, blank=True)
    pi_comment = models.TextField(blank=True)

    # Derek (£5,000+)
    derek_emailed_at = models.DateTimeField(null=True, blank=True)
    derek_decision_at = models.DateTimeField(null=True, blank=True)

    # Ordering
    purchaser = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    ordered_at = models.DateField(null=True, blank=True)
    order_reference = models.CharField(max_length=100, blank=True, help_text="PO / order number")
    actual_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                       help_text="£, total actually paid on the procurement site")
    replacement_purchased = models.BooleanField(default=False)
    replacement_details = models.TextField(blank=True, help_text="What was bought instead, and why")

    # Set on purchases imported from the old spreadsheet: "PO-4318152", or "PR-2912513 <supplier>" for a
    # requisition with no matching PO. Lets the import be re-run without creating duplicates.
    import_key = models.CharField(max_length=150, blank=True, db_index=True)

    # Receiving
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    received_at = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.item_name} (£{self.total})"

    def get_absolute_url(self):
        return reverse("request_detail", args=[self.pk])

    def recalculate(self):
        self.total = (self.unit_price or 0) * (self.quantity or 0) + (self.shipping or 0)
        self.tier = tier_for(self.total).number
        if self.tier > 1:
            self.method = Method.PURCHASE_FORM

    def save(self, *args, **kwargs):
        self.recalculate()
        super().save(*args, **kwargs)

    @property
    def tier_info(self):
        return get_tier(self.tier)

    @property
    def status_colour(self):
        return STATUS_COLOURS.get(self.status, "secondary")

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    @property
    def amount(self):
        """What this purchase costs the grant: the actual price once known, otherwise the requested total."""
        return self.actual_price if self.actual_price is not None else self.total

    @property
    def purchase_date(self):
        return self.ordered_at or timezone.localdate(self.created_at)

    @property
    def spending_bucket(self):
        if self.status in SPENT_STATUSES:
            return "spent"
        if self.status in COMMITTED_STATUSES:
            return "committed"
        if self.status in PENDING_STATUSES:
            return "pending"
        return None

    @property
    def is_imported(self):
        return bool(self.import_key)

    @property
    def unconfirmed_import(self):
        """Imported from an approved requisition with no PO found - may not have been bought."""
        return self.import_key.startswith("PR-")

    @property
    def pi_approved(self):
        return self.pi_decision_at is not None and self.status not in (
            Status.AWAITING_PI, Status.CHANGES_REQUESTED, Status.REJECTED)


def attachment_path(instance, filename):
    return f"requests/{instance.request_id}/{instance.kind}/{filename}"


class Attachment(models.Model):
    class Kind(models.TextChoices):
        QUOTE = "quote", "Quote"
        SPEC = "spec", "Spec / datasheet"
        RECEIPT = "receipt", "Receipt"
        DEREK = "derek", "Derek's approval"
        PACKING_SLIP = "packing_slip", "Packing slip"
        OTHER = "other", "Other"

    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(upload_to=attachment_path)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return self.filename

    @property
    def filename(self):
        return self.file.name.rsplit("/", 1)[-1]


class Event(models.Model):
    """Audit trail and discussion thread for a request - this replaces the approval email."""

    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.request_id}: {self.action}"
