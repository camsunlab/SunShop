from decimal import Decimal

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Method, PurchaseRequest
from .rules import tier_for


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.RadioSelect):
                continue
            css = "form-select" if isinstance(widget, forms.Select) else "form-control"
            widget.attrs.setdefault("class", css)


class PurchaseRequestForm(BootstrapMixin, forms.ModelForm):
    quote = forms.FileField(required=False, help_text="Required for £2,000 and over.")
    spec_file = forms.FileField(required=False, label="Spec / datasheet (optional)")
    receipt = forms.FileField(required=False, help_text="Required if you're claiming a reimbursement.")

    class Meta:
        model = PurchaseRequest
        fields = ("item_name", "spec", "vendor", "purchase_link", "quantity", "unit_price", "shipping",
                  "grant_code", "project", "method", "needed_by", "notes")
        labels = {"unit_price": "Unit price (£)", "shipping": "Shipping / other (£)", "spec": "Spec & justification",
                  "method": "How will this be bought?"}
        widgets = {
            "spec": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "method": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "shipping": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "quantity": forms.NumberInput(attrs={"min": "1"}),
        }

    def __init__(self, *args, has_quote=False, has_receipt=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_quote = has_quote
        self.has_receipt = has_receipt

    def clean(self):
        data = super().clean()
        qty = data.get("quantity") or 0
        total = (data.get("unit_price") or Decimal(0)) * qty + (data.get("shipping") or Decimal(0))
        tier = tier_for(total)
        if tier.number > 1 and data.get("method") != Method.PURCHASE_FORM:
            data["method"] = Method.PURCHASE_FORM
        if tier.needs_quote and not (data.get("quote") or self.has_quote):
            self.add_error("quote", f"A quote is required for purchases of £2,000 and over (this one is £{total:.2f}).")
        if data.get("method") == Method.REIMBURSEMENT and not (data.get("receipt") or self.has_receipt):
            self.add_error("receipt", "Please attach your receipt for a reimbursement.")
        return data


class NoteForm(BootstrapMixin, forms.Form):
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Comment")


class OrderedForm(NoteForm):
    order_reference = forms.CharField(required=False, label="PO / order number")
    ordered_at = forms.DateField(required=False, label="Order date", widget=forms.DateInput(attrs={"type": "date"}))
    actual_price = forms.DecimalField(required=False, min_value=0, decimal_places=2, label="Actual price paid (£)",
                                      help_text="Total from the procurement site, if different from the request.")
    replacement_purchased = forms.BooleanField(required=False, label="Replacement purchased instead",
                                               widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    replacement_details = forms.CharField(required=False, label="What was bought instead?",
                                          widget=forms.Textarea(attrs={"rows": 2}))

    field_order = ["order_reference", "ordered_at", "actual_price", "replacement_purchased", "replacement_details", "note"]

    def clean(self):
        data = super().clean()
        if data.get("replacement_purchased") and not data.get("replacement_details"):
            self.add_error("replacement_details", "Please describe the replacement item.")
        return data


class ReceivedForm(NoteForm):
    received_at = forms.DateField(required=False, label="Date received", widget=forms.DateInput(attrs={"type": "date"}))
    packing_slip = forms.FileField(required=False)


class DerekForm(NoteForm):
    derek_file = forms.FileField(required=False, label="Derek's reply (optional)")


class CommentForm(BootstrapMixin, forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Ask a question or add detail…"}), label="")


class SignUpForm(BootstrapMixin, UserCreationForm):
    first_name = forms.CharField(max_length=30)
    last_name = forms.CharField(max_length=30)
    email = forms.EmailField(help_text="Notifications are sent here.")

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "password1", "password2")
