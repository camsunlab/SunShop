import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from . import workflow
from .models import Method, PurchaseRequest, Status
from .rules import tier_for

MEDIA = tempfile.mkdtemp()


class TierTests(TestCase):
    def test_boundaries(self):
        cases = {"0": 1, "200": 1, "200.01": 2, "1999.99": 2, "2000": 3, "4999.99": 3, "5000": 4, "25000": 4}
        for total, tier in cases.items():
            self.assertEqual(tier_for(Decimal(total)).number, tier, total)


@override_settings(MEDIA_ROOT=MEDIA, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WorkflowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.student = User.objects.create_user("student", "student@lab.ac.uk", "pw")
        self.pi = User.objects.create_user("shijing", "shijing@lab.ac.uk", "pw", first_name="Shijing")
        self.buyer = User.objects.create_user("clara", "clara@lab.ac.uk", "pw", first_name="Clara")
        self.pi.groups.add(Group.objects.get(name="PI"))
        self.buyer.groups.add(Group.objects.get(name="Purchaser"))

    def make(self, unit_price, quantity=1, method=Method.PURCHASE_FORM):
        req = PurchaseRequest(requester=self.student, item_name="Thing", spec="spec", vendor="V",
                              unit_price=Decimal(unit_price), quantity=quantity, grant_code="G1", method=method)
        workflow.submit(req, self.student)
        return req

    def test_small_purchase_skips_pi(self):
        self.assertEqual(self.make("50").status, Status.TO_ORDER)
        self.assertEqual(self.make("50", method=Method.STORE).status, Status.BOUGHT_AT_STORE)
        self.assertEqual(self.make("50", method=Method.REIMBURSEMENT).status, Status.AWAITING_REIMBURSEMENT)

    def test_total_uses_quantity(self):
        req = self.make("150", quantity=2)
        self.assertEqual(req.total, Decimal("300.00"))
        self.assertEqual(req.status, Status.AWAITING_PI)

    def test_medium_needs_pi_then_purchaser(self):
        req = self.make("500")
        self.assertEqual(req.status, Status.AWAITING_PI)
        self.assertEqual(mail.outbox[-1].to, ["shijing@lab.ac.uk"])
        with self.assertRaises(workflow.WorkflowError):
            workflow.perform(req, self.student, "approve")
        workflow.perform(req, self.pi, "approve")
        self.assertEqual(req.status, Status.TO_ORDER)
        workflow.perform(req, self.buyer, "ordered", {"order_reference": "PO123"})
        self.assertEqual(req.status, Status.ORDERED)
        workflow.perform(req, self.student, "received")
        self.assertEqual(req.status, Status.RECEIVED)

    def test_large_ccs_purchaser(self):
        mail.outbox.clear()
        self.make("3000")
        self.assertEqual(sorted(mail.outbox[0].to), ["clara@lab.ac.uk", "shijing@lab.ac.uk"])

    def test_major_goes_via_derek(self):
        req = self.make("6000")
        workflow.perform(req, self.pi, "approve")
        self.assertEqual(req.status, Status.AWAITING_DEREK)
        self.assertNotIn("ordered", workflow.available_actions(req, self.buyer))
        self.assertIn("cc=clara%40lab.ac.uk", workflow.derek_mailto(req))
        workflow.perform(req, self.student, "derek_emailed")
        workflow.perform(req, self.buyer, "derek_approved")
        self.assertEqual(req.status, Status.TO_ORDER)

    def test_changes_requested_then_resubmit(self):
        req = self.make("500")
        with self.assertRaises(workflow.WorkflowError):
            workflow.perform(req, self.pi, "request_changes", {"note": ""})
        workflow.perform(req, self.pi, "request_changes", {"note": "Cheaper vendor?"})
        self.assertTrue(workflow.can_edit(req, self.student))
        req.unit_price = Decimal("150")
        workflow.submit(req, self.student, resubmit=True)
        self.assertEqual(req.status, Status.TO_ORDER)
        self.assertIsNone(req.pi_decision_at)

    def test_approved_request_cannot_be_edited(self):
        req = self.make("500")
        workflow.perform(req, self.pi, "approve")
        self.assertFalse(workflow.can_edit(req, self.student))

    def test_quote_required_from_2000(self):
        self.client.force_login(self.student)
        data = {"item_name": "Laser", "spec": "s", "vendor": "V", "quantity": 1, "unit_price": "2500",
                "shipping": "0", "grant_code": "G", "method": "purchase_form"}
        resp = self.client.post(reverse("request_new"), data)
        self.assertContains(resp, "A quote is required")
        data["quote"] = SimpleUploadedFile("quote.pdf", b"%PDF-1.4")
        resp = self.client.post(reverse("request_new"), data)
        req = PurchaseRequest.objects.get()
        self.assertRedirects(resp, req.get_absolute_url())
        self.assertEqual(req.attachments.get().kind, "quote")
        # Attachments are only visible to people involved
        other = User.objects.create_user("other", "", "pw")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("attachment", args=[req.attachments.get().pk])).status_code, 403)

    def test_pages_render(self):
        req = self.make("6000")
        workflow.perform(req, self.pi, "approve")
        for user in (self.student, self.pi, self.buyer):
            self.client.force_login(user)
            for name in ("home", "request_new", "all_requests"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
            self.assertEqual(self.client.get(req.get_absolute_url()).status_code, 200)
        self.client.force_login(self.pi)
        self.assertEqual(self.client.get(reverse("approvals")).status_code, 200)
        self.client.force_login(self.buyer)
        self.assertContains(self.client.get(reverse("purchasing_queue")), "Awaiting Derek")
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("approvals")).status_code, 302)

    def test_ordered_with_actual_price_and_replacement(self):
        req = self.make("500")
        workflow.perform(req, self.pi, "approve")
        workflow.perform(req, self.buyer, "ordered", {"actual_price": Decimal("472.10"),
                                                      "replacement_purchased": True,
                                                      "replacement_details": "Equivalent model from Fisher"})
        req.refresh_from_db()
        self.assertEqual(req.actual_price, Decimal("472.10"))
        self.assertTrue(req.replacement_purchased)
        self.assertIn("replacement", mail.outbox[-1].subject)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(req.get_absolute_url()), "Equivalent model from Fisher")

    def test_replacement_needs_details(self):
        req = self.make("500")
        workflow.perform(req, self.pi, "approve")
        self.client.force_login(self.buyer)
        self.client.post(reverse("request_action", args=[req.pk, "ordered"]),
                         {"ordered-replacement_purchased": "on", "ordered-replacement_details": ""})
        req.refresh_from_db()
        self.assertEqual(req.status, Status.TO_ORDER)

    def test_action_view(self):
        req = self.make("500")
        self.client.force_login(self.pi)
        self.client.post(reverse("request_action", args=[req.pk, "approve"]), {"approve-note": "ok"})
        req.refresh_from_db()
        self.assertEqual(req.status, Status.TO_ORDER)
        self.assertEqual(req.pi_comment, "ok")
