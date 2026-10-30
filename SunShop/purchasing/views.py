from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import workflow
from .forms import (CommentForm, DerekForm, NoteForm, OrderedForm, PurchaseRequestForm,
                    ReceivedForm, SignUpForm)
from .models import Attachment, OPEN_STATUSES, PurchaseRequest, Status
from .rules import TIERS

# action name -> (button label, bootstrap colour, form class, attachment field, attachment kind)
ACTIONS = {
    "approve": ("Approve", "success", NoteForm, None, None),
    "request_changes": ("Request changes", "warning", NoteForm, None, None),
    "reject": ("Reject", "danger", NoteForm, None, None),
    "derek_emailed": ("I've emailed Derek", "primary", NoteForm, None, None),
    "derek_approved": ("Derek approved", "success", DerekForm, "derek_file", Attachment.Kind.DEREK),
    "derek_declined": ("Derek declined", "danger", DerekForm, "derek_file", Attachment.Kind.DEREK),
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
        "comment_form": comment_form,
        "derek_mailto": workflow.derek_mailto(req) if req.status == Status.AWAITING_DEREK else None,
    })


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
            ("Awaiting Derek", "£5,000+ approved by the PI – waiting on Derek.", q.filter(status=Status.AWAITING_DEREK)),
            ("Reimbursements to process", "Small purchases paid by the requester.", q.filter(status=Status.AWAITING_REIMBURSEMENT)),
            ("Ordered – awaiting delivery", "", q.filter(status=Status.ORDERED)),
            ("In discussion (£2,000 – £4,999)", "You're cc'd on these while the PI reviews.",
             q.filter(status__in=[Status.AWAITING_PI, Status.CHANGES_REQUESTED], tier=3)),
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
