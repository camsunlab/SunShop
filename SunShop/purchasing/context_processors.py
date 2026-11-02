from .models import PurchaseRequest, Status
from .workflow import is_pi, is_purchaser


def roles(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    context = {"is_pi": is_pi(user), "is_purchaser": is_purchaser(user)}
    if context["is_pi"]:
        context["nav_pi_count"] = PurchaseRequest.objects.filter(status=Status.AWAITING_PI).count()
    if context["is_purchaser"]:
        context["nav_purchaser_count"] = PurchaseRequest.objects.filter(
            status__in=[Status.TO_ORDER, Status.AWAITING_REIMBURSEMENT]).count()
    return context
