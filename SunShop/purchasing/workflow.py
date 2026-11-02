"""
Request lifecycle. Every status change goes through here so the rules, the audit
trail and the email notifications stay in one place.
"""
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from .models import Event, Method, Status

PI_GROUP = "PI"
PURCHASER_GROUP = "Purchaser"


class WorkflowError(Exception):
    pass


# ---------------------------------------------------------------- roles

def is_pi(user):
    return user.is_superuser or user.groups.filter(name=PI_GROUP).exists()


def is_purchaser(user):
    return user.is_superuser or user.groups.filter(name=PURCHASER_GROUP).exists()


def _group_emails(group):
    users = get_user_model().objects.filter(groups__name=group, is_active=True).exclude(email="")
    return list(users.values_list("email", flat=True))


def pi_emails():
    return _group_emails(PI_GROUP)


def purchaser_emails():
    return _group_emails(PURCHASER_GROUP)


# ---------------------------------------------------------------- permissions

def can_edit(req, user):
    if req.requester_id != user.id and not is_purchaser(user):
        return False
    if req.status in (Status.AWAITING_PI, Status.CHANGES_REQUESTED):
        return True
    # Small purchases can be tweaked until they're actioned, as no approval is involved.
    return req.pi_decision_at is None and req.status in (Status.TO_ORDER, Status.AWAITING_REIMBURSEMENT)


def available_actions(req, user):
    """Names of actions `user` may take on `req` right now."""
    mine = req.requester_id == user.id
    actions = []
    if req.status == Status.AWAITING_PI and is_pi(user):
        actions += ["approve", "request_changes", "reject"]
    if req.status == Status.AWAITING_DEREK:
        if (mine or is_purchaser(user)) and req.derek_emailed_at is None:
            actions.append("derek_emailed")
        if is_purchaser(user):
            actions += ["derek_approved", "derek_declined"]
    if req.status == Status.TO_ORDER and is_purchaser(user):
        actions.append("ordered")
    if req.status == Status.ORDERED and (mine or is_purchaser(user)):
        actions.append("received")
    if req.status == Status.AWAITING_REIMBURSEMENT and is_purchaser(user):
        actions.append("reimbursed")
    if req.is_open and req.status != Status.ORDERED and (mine or is_purchaser(user)):
        actions.append("cancel")
    elif req.unconfirmed_import and req.status != Status.CANCELLED and is_purchaser(user):
        actions.append("cancel")  # imported PR that turns out never to have been ordered
    return actions


# ---------------------------------------------------------------- transitions

def log(req, user, action, message=""):
    return Event.objects.create(request=req, actor=user, action=action, message=message)


def route(req):
    """Set the starting status for a new / resubmitted request based on its tier."""
    req.recalculate()
    if req.tier_info.needs_pi:
        req.status = Status.AWAITING_PI
    elif req.method == Method.STORE:
        req.status = Status.BOUGHT_AT_STORE
    elif req.method == Method.REIMBURSEMENT:
        req.status = Status.AWAITING_REIMBURSEMENT
    else:
        req.status = Status.TO_ORDER
    # Any earlier approval no longer applies to a changed request.
    req.pi_decision_at = req.pi_decision_by = None
    req.pi_comment = ""


def submit(req, user, resubmit=False):
    route(req)
    req.save()
    tier = req.tier_info
    log(req, user, "resubmitted" if resubmit else "submitted",
        f"£{req.total:,.2f} – {tier.label} purchase ({tier.range_label}).")

    if tier.needs_pi:
        cc = purchaser_emails() if tier.cc_purchaser else []
        notify(req, pi_emails(), f"Approval needed: {req.item_name} (£{req.total:,.2f})",
               f"{user_name(req.requester)} has {'re' if resubmit else ''}submitted a purchase request that needs your approval.",
               cc=cc)
    elif req.status in (Status.TO_ORDER, Status.AWAITING_REIMBURSEMENT):
        notify(req, purchaser_emails(), f"New small purchase: {req.item_name} (£{req.total:,.2f})",
               f"{user_name(req.requester)} submitted a small purchase – {req.get_status_display().lower()}.")


def perform(req, user, action, data=None, files=None):
    """Run `action` on `req` as `user`. `data` is the cleaned form data for that action."""
    if action not in available_actions(req, user):
        raise WorkflowError("You can't do that on this request right now.")
    data = data or {}
    note = data.get("note", "")
    now = timezone.now()

    if action == "approve":
        req.pi_decision_by, req.pi_decision_at, req.pi_comment = user, now, note
        req.status = Status.AWAITING_DEREK if req.tier_info.needs_derek else Status.TO_ORDER
        req.save()
        log(req, user, "approved", note)
        notify(req, [req.requester.email], f"Approved: {req.item_name}",
               f"{user_name(user)} approved your request."
               + (" Next step: email Derek to request the purchase (link on the request page)."
                  if req.tier_info.needs_derek else " It's now with the purchaser to order."))
        if req.status == Status.TO_ORDER:
            notify(req, purchaser_emails(), f"Ready to order: {req.item_name} (£{req.total:,.2f})",
                   f"Approved by {user_name(user)}.")

    elif action in ("request_changes", "reject"):
        if not note:
            raise WorkflowError("Please add a comment explaining why.")
        req.pi_decision_by, req.pi_decision_at, req.pi_comment = user, now, note
        req.status = Status.CHANGES_REQUESTED if action == "request_changes" else Status.REJECTED
        req.save()
        log(req, user, "changes requested" if action == "request_changes" else "rejected", note)
        notify(req, [req.requester.email], f"{req.get_status_display()}: {req.item_name}",
               f"{user_name(user)}: {note}")

    elif action == "derek_emailed":
        req.derek_emailed_at = now
        req.save()
        log(req, user, "emailed Derek", note)

    elif action in ("derek_approved", "derek_declined"):
        req.derek_decision_at = now
        req.status = Status.TO_ORDER if action == "derek_approved" else Status.REJECTED
        req.save()
        log(req, user, "Derek approved" if action == "derek_approved" else "Derek declined", note)
        notify(req, [req.requester.email], f"{'Derek approved' if action == 'derek_approved' else 'Derek declined'}: {req.item_name}",
               note or "See the request page for details.")

    elif action == "ordered":
        req.purchaser = user
        req.ordered_at = data.get("ordered_at") or now.date()
        req.order_reference = data.get("order_reference", "")
        req.actual_price = data.get("actual_price")
        req.replacement_purchased = bool(data.get("replacement_purchased"))
        req.replacement_details = data.get("replacement_details", "") if req.replacement_purchased else ""
        req.status = Status.ORDERED
        req.save()
        details = [
            req.order_reference and f"Ref {req.order_reference}",
            req.actual_price is not None and f"Actual price £{req.actual_price:,.2f}",
            req.replacement_purchased and f"Replacement purchased instead: {req.replacement_details}",
            note,
        ]
        log(req, user, "ordered", " – ".join(filter(None, details)))
        body = f"{user_name(user)} placed the order."
        if req.replacement_purchased:
            body += f"\n\nThe requested item wasn't available, so a replacement was purchased instead:\n{req.replacement_details}"
        notify(req, [req.requester.email], f"Ordered{' (replacement)' if req.replacement_purchased else ''}: {req.item_name}", body)

    elif action == "received":
        req.received_by = user
        req.received_at = data.get("received_at") or now.date()
        req.status = Status.RECEIVED
        req.save()
        log(req, user, "received", note)

    elif action == "reimbursed":
        req.status = Status.REIMBURSED
        req.save()
        log(req, user, "reimbursed", note)
        notify(req, [req.requester.email], f"Reimbursed: {req.item_name}", "Your reimbursement has been processed.")

    elif action == "cancel":
        req.status = Status.CANCELLED
        req.save()
        log(req, user, "cancelled", note)

    return req


def comment(req, user, message):
    log(req, user, "comment", message)
    recipients = {req.requester.email}
    if req.tier_info.needs_pi:
        recipients.update(pi_emails())
    if req.tier >= 3 or req.status not in (Status.AWAITING_PI, Status.CHANGES_REQUESTED):
        recipients.update(purchaser_emails())
    recipients.discard(user.email)
    notify(req, recipients, f"New comment on: {req.item_name}", f"{user_name(user)}: {message}")


# ---------------------------------------------------------------- email

def user_name(user):
    return user.get_full_name() or user.username


def site_url(req):
    return settings.SUNSHOP_SITE_URL.rstrip("/") + req.get_absolute_url()


def notify(req, to, subject, body, cc=()):
    to = [e for e in to if e]
    cc = [e for e in cc if e and e not in to]
    if not to and not cc:
        return
    body = f"{body}\n\n#{req.pk} {req.item_name} – £{req.total:,.2f}\n{site_url(req)}\n"
    # cc'd people are sent the same message; send_mail has no cc argument.
    send_mail(f"[SunShop] {subject}", body, None, to + cc, fail_silently=True)


def derek_mailto(req):
    """Pre-filled email for the requester to send to Derek (purchaser cc'd)."""
    approval = req.events.filter(action="approved").last()
    body = "\n".join([
        "Hi Derek,",
        "",
        f"I'd like to request the following purchase, which has been approved by "
        f"{user_name(req.pi_decision_by) if req.pi_decision_by else 'the PI'}"
        f"{' on ' + approval.created_at.strftime('%d %b %Y') if approval else ''}.",
        "",
        f"Item: {req.item_name}",
        f"Vendor: {req.vendor}",
        f"Quantity: {req.quantity}",
        f"Total: £{req.total:,.2f}",
        f"Grant code: {req.grant_code}",
        "",
        "Spec:",
        req.spec,
        "",
        f"Full details, quote and approval record: {site_url(req)}",
        "",
        "Thanks,",
        user_name(req.requester),
    ])
    params = f"subject={quote(f'Purchase request: {req.item_name} (£{req.total:,.2f})')}&body={quote(body)}"
    cc = ",".join(purchaser_emails())
    if cc:
        params = f"cc={quote(cc)}&" + params
    return f"mailto:{quote(settings.SUNSHOP_DEREK_EMAIL)}?{params}"
