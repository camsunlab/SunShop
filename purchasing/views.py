import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import workflow
from .forms import (AttachmentForm, CommentForm, NoteForm, OrderedForm, PurchaseRequestForm,
                    ReceivedForm, SignUpForm)
from .models import Attachment, OPEN_STATUSES, PurchaseRequest, Status
from .rules import TIERS

# action name -> (button label, bootstrap colour, form class, attachment field, attachment kind)
ACTIONS = {
    "approve": ("Approve", "success", NoteForm, None, None),
    "request_changes": ("Request changes", "warning", NoteForm, None, None),
    "reject": ("Reject", "danger", NoteForm, None, None),
    "ordered": ("Mark as ordered", "success", OrderedForm, None, None),
    "received": ("Mark as received", "success", ReceivedForm, "packing_slip", Attachment.Kind.PACKING_SLIP),
    "reimbursed": ("Mark as reimbursed", "success", NoteForm, None, None),
    "cancel": ("Cancel request", "outline-secondary", NoteForm, None, None),
}


def attach(req, user, kind, file):
    if file:
        Attachment.objects.create(request=req, kind=kind, file=file, uploaded_by=user)


def can_view(req, user):
    return req.requester_id == user.id or workflow.is_pi(user) or workflow.is_purchaser(user) or user.is_staff


def can_upload(req, user):
    """The requester, PIs, the purchaser and admins can add files to a request."""
    return req.requester_id == user.id or workflow.is_pi(user) or workflow.is_purchaser(user) or user.is_staff


def get_request_for(user, pk):
    req = get_object_or_404(PurchaseRequest.objects.select_related("requester"), pk=pk)
    if not can_view(req, user):
        raise PermissionDenied
    return req


# ---------------------------------------------------------------- pages

@login_required
def home(request):
    user = request.user
    mine = PurchaseRequest.objects.filter(requester=user)
    context = {
        "my_open": mine.filter(status__in=OPEN_STATUSES),
        "my_recent": mine.exclude(status__in=OPEN_STATUSES)[:5],
        "tiers": TIERS,
    }
    return render(request, "purchasing/home.html", context)


@login_required
def request_new(request):
    form = PurchaseRequestForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            req = form.save(commit=False)
            req.requester = request.user
            req.save()
            attach(req, request.user, Attachment.Kind.QUOTE, form.cleaned_data.get("quote"))
            attach(req, request.user, Attachment.Kind.SPEC, form.cleaned_data.get("spec_file"))
            attach(req, request.user, Attachment.Kind.RECEIPT, form.cleaned_data.get("receipt"))
            workflow.submit(req, request.user)
        messages.success(request, f"Request submitted – {req.get_status_display().lower()}.")
        return redirect(req)
    return render(request, "purchasing/request_form.html", {"form": form, "tiers": TIERS})


@login_required
def request_edit(request, pk):
    req = get_request_for(request.user, pk)
    if not workflow.can_edit(req, request.user):
        messages.error(request, "This request can no longer be edited.")
        return redirect(req)
    kinds = set(req.attachments.values_list("kind", flat=True))
    form = PurchaseRequestForm(request.POST or None, request.FILES or None, instance=req,
                               has_quote=Attachment.Kind.QUOTE in kinds,
                               has_receipt=Attachment.Kind.RECEIPT in kinds)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            req = form.save()
            attach(req, request.user, Attachment.Kind.QUOTE, form.cleaned_data.get("quote"))
            attach(req, request.user, Attachment.Kind.SPEC, form.cleaned_data.get("spec_file"))
            attach(req, request.user, Attachment.Kind.RECEIPT, form.cleaned_data.get("receipt"))
            workflow.submit(req, request.user, resubmit=True)
        messages.success(request, "Request updated and resubmitted.")
        return redirect(req)
    return render(request, "purchasing/request_form.html", {"form": form, "tiers": TIERS, "req": req})


@login_required
def request_detail(request, pk):
    req = get_request_for(request.user, pk)
    comment_form = CommentForm(request.POST or None)
    if request.method == "POST" and comment_form.is_valid():
        workflow.comment(req, request.user, comment_form.cleaned_data["message"])
        return redirect(req)

    actions = [
        {"name": name, "label": ACTIONS[name][0], "style": ACTIONS[name][1],
         "form": ACTIONS[name][2](prefix=name)}
        for name in workflow.available_actions(req, request.user)
    ]
    return render(request, "purchasing/request_detail.html", {
        "req": req,
        "tier": req.tier_info,
        "actions": actions,
        "can_edit": workflow.can_edit(req, request.user),
        "events": req.events.select_related("actor"),
        "attachments": req.attachments.all(),
        "missing_quote": req.tier_info.needs_quote and not req.attachments.filter(kind=Attachment.Kind.QUOTE).exists(),
        "upload_form": AttachmentForm(prefix="upload") if can_upload(req, request.user) else None,
        "comment_form": comment_form,
    })


@login_required
@require_POST
def request_upload(request, pk):
    req = get_request_for(request.user, pk)
    if not can_upload(req, request.user):
        raise PermissionDenied
    form = AttachmentForm(request.POST, request.FILES, prefix="upload")
    if not form.is_valid():
        messages.error(request, "Choose a file to upload.")
        return redirect(req)
    with transaction.atomic():
        att = Attachment.objects.create(request=req, kind=form.cleaned_data["kind"], file=form.cleaned_data["file"],
                                        uploaded_by=request.user)
        req.events.create(actor=request.user, action="file added", message=f"{att.get_kind_display()}: {att.filename}")
    messages.success(request, f"{att.get_kind_display()} uploaded.")
    return redirect(req)


@login_required
@require_POST
def request_action(request, pk, action):
    req = get_request_for(request.user, pk)
    if action not in ACTIONS:
        raise PermissionDenied
    _, _, form_class, file_field, file_kind = ACTIONS[action]
    form = form_class(request.POST, request.FILES, prefix=action)
    if not form.is_valid():
        messages.error(request, "Please check the form and try again.")
        return redirect(req)
    try:
        with transaction.atomic():
            workflow.perform(req, request.user, action, form.cleaned_data)
            if file_field:
                attach(req, request.user, file_kind, form.cleaned_data.get(file_field))
    except workflow.WorkflowError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"#{req.pk}: {req.get_status_display()}.")
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(req)


@login_required
@user_passes_test(workflow.is_pi)
def approvals(request):
    return render(request, "purchasing/approvals.html", {
        "pending": PurchaseRequest.objects.filter(status=Status.AWAITING_PI).order_by("created_at"),
        "decided": PurchaseRequest.objects.filter(pi_decision_by=request.user).order_by("-pi_decision_at")[:10],
    })


@login_required
@user_passes_test(workflow.is_purchaser)
def purchasing_queue(request):
    q = PurchaseRequest.objects.select_related("requester").order_by("created_at")
    return render(request, "purchasing/queue.html", {
        "sections": [
            ("Ready to order", "Approved (or under £200) – place the order.", q.filter(status=Status.TO_ORDER)),
            ("Reimbursements to process", "Small purchases paid by the requester.", q.filter(status=Status.AWAITING_REIMBURSEMENT)),
            ("Ordered – awaiting delivery", "", q.filter(status=Status.ORDERED)),
            ("In discussion (£2,000+)", "You're cc'd on these while the PI reviews.",
             q.filter(status__in=[Status.AWAITING_PI, Status.CHANGES_REQUESTED], tier__gte=3)),
        ],
    })


@login_required
def all_requests(request):
    user = request.user
    qs = PurchaseRequest.objects.select_related("requester")
    if not (workflow.is_pi(user) or workflow.is_purchaser(user) or user.is_staff):
        qs = qs.filter(requester=user)
    query, status, tier = request.GET.get("q", ""), request.GET.get("status", ""), request.GET.get("tier", "")
    if query:
        qs = qs.filter(Q(item_name__icontains=query) | Q(vendor__icontains=query) | Q(project__icontains=query)
                       | Q(grant_code__icontains=query) | Q(order_reference__icontains=query)
                       | Q(requester__first_name__icontains=query) | Q(requester__last_name__icontains=query))
    if status:
        qs = qs.filter(status=status)
    if tier:
        qs = qs.filter(tier=tier)
    return render(request, "purchasing/all_requests.html", {
        "requests": qs, "q": query, "status": status, "tier": tier,
        "statuses": Status.choices, "tiers": TIERS,
    })


def can_see_spending(user):
    return workflow.is_pi(user) or workflow.is_purchaser(user) or user.is_staff


@login_required
@user_passes_test(can_see_spending)
def spending(request):
    """Lab spending by year and grant code, with CSV export."""
    all_qs = PurchaseRequest.objects.exclude(status__in=[Status.REJECTED, Status.CANCELLED])
    years = sorted({d.year for d in all_qs.dates("created_at", "year")}
                   | {d.year for d in all_qs.exclude(ordered_at=None).dates("ordered_at", "year")}, reverse=True)
    this_year = timezone.localdate().year
    if this_year not in years:
        years.insert(0, this_year)
    grant_codes = sorted({c.strip().upper() for c in all_qs.values_list("grant_code", flat=True) if c.strip()})

    year = request.GET.get("year", str(this_year))
    grant = request.GET.get("grant", "")
    qs = all_qs.select_related("requester")
    if year != "all":
        year = int(year) if year.isdigit() else this_year
        # A purchase belongs to the year it was ordered, or the year it was requested if not ordered yet.
        qs = qs.filter(Q(ordered_at__year=year) | Q(ordered_at=None, created_at__year=year))
    purchases = sorted(qs, key=lambda r: (r.purchase_date, r.pk), reverse=True)
    if grant:
        purchases = [r for r in purchases if r.grant_code.strip().upper() == grant.strip().upper()]

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="sunshop-spending-{year}{"-" + grant if grant else ""}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Request #", "Date", "Grant code", "Project", "Item", "Vendor", "Requested by", "Quantity",
                         "Requested total (£)", "Actual price (£)", "Amount (£)", "Counted as", "Status",
                         "PO / order number", "Replacement purchased", "Replacement details"])
        for r in purchases:
            writer.writerow([r.pk, r.purchase_date.isoformat(), r.grant_code, r.project, r.item_name, r.vendor,
                             r.requester.get_full_name() or r.requester.username, r.quantity, r.total,
                             "" if r.actual_price is None else r.actual_price, r.amount, r.spending_bucket,
                             r.get_status_display(), r.order_reference, "Yes" if r.replacement_purchased else "",
                             r.replacement_details])
        return response

    zero = Decimal("0.00")
    by_grant = {}
    totals = {"count": 0, "spent": zero, "committed": zero, "pending": zero}
    for r in purchases:
        code = r.grant_code.strip().upper() or "(no grant code)"
        row = by_grant.setdefault(code, {"code": code, "count": 0, "spent": zero, "committed": zero, "pending": zero})
        for target in (row, totals):
            target["count"] += 1
            target[r.spending_bucket] += r.amount

    return render(request, "purchasing/spending.html", {
        "years": years, "year": year, "grant_codes": grant_codes, "grant": grant,
        "summary": sorted(by_grant.values(), key=lambda row: row["spent"] + row["committed"], reverse=True),
        "totals": totals, "purchases": purchases,
        "csv_query": request.GET.urlencode(),
    })


@login_required
def attachment(request, pk):
    att = get_object_or_404(Attachment.objects.select_related("request"), pk=pk)
    if not can_view(att.request, request.user):
        raise PermissionDenied
    return FileResponse(att.file.open("rb"), filename=att.filename)


@login_required
@user_passes_test(lambda u: u.is_staff)
def register_user(request):
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Account created for {user.get_full_name()}.")
        return redirect("home")
    return render(request, "purchasing/register.html", {"form": form})
