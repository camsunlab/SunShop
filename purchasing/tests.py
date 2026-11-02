import os
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.management import call_command
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

    def test_add_quote_to_finished_request(self):
        """Quotes can be added later to any request (e.g. imported history), by staff but not other students."""
        req = self.make("6000")
        req.status = Status.RECEIVED
        req.save()
        url = reverse("request_upload", args=[req.pk])
        self.client.force_login(self.student)
        self.assertContains(self.client.get(req.get_absolute_url()), "No quote uploaded yet")
        other = User.objects.create_user("other", "o@lab.ac.uk", "pw")
        self.client.force_login(other)
        self.assertEqual(self.client.post(url, {"upload-kind": "quote", "upload-file": SimpleUploadedFile("q.pdf", b"%PDF")}).status_code, 403)
        self.client.force_login(self.buyer)
        self.client.post(url, {"upload-kind": "quote", "upload-file": SimpleUploadedFile("q.pdf", b"%PDF")})
        self.assertEqual(req.attachments.get().kind, "quote")
        self.assertTrue(req.events.filter(action="file added").exists())
        self.assertNotContains(self.client.get(req.get_absolute_url()), "No quote uploaded yet")

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

    def test_major_goes_straight_to_purchaser(self):
        mail.outbox.clear()
        req = self.make("6000")
        self.assertEqual(sorted(mail.outbox[0].to), ["clara@lab.ac.uk", "shijing@lab.ac.uk"])
        workflow.perform(req, self.pi, "approve")
        self.assertEqual(req.status, Status.TO_ORDER)
        self.assertIn("ordered", workflow.available_actions(req, self.buyer))

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
        self.assertContains(self.client.get(reverse("purchasing_queue")), "Ready to order")
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

    def test_spending_by_year_and_grant(self):
        a = self.make("100")  # small, ready to order -> committed
        b = self.make("500")
        workflow.perform(b, self.pi, "approve")
        workflow.perform(b, self.buyer, "ordered", {"actual_price": Decimal("450")})  # spent, actual price
        c = self.make("300")  # awaiting PI -> pending
        c.grant_code = "g2 "
        c.save()
        d = self.make("80")
        workflow.perform(d, self.student, "cancel")  # not counted
        old = self.make("50", method=Method.STORE)
        PurchaseRequest.objects.filter(pk=old.pk).update(created_at=old.created_at.replace(year=2024))

        self.client.force_login(self.pi)
        resp = self.client.get(reverse("spending"))
        totals = resp.context["totals"]
        self.assertEqual((totals["spent"], totals["committed"], totals["pending"], totals["count"]),
                         (Decimal("450"), Decimal("100"), Decimal("300"), 3))
        self.assertEqual({row["code"] for row in resp.context["summary"]}, {"G1", "G2"})

        resp = self.client.get(reverse("spending"), {"grant": "G2"})
        self.assertEqual([r.pk for r in resp.context["purchases"]], [c.pk])
        resp = self.client.get(reverse("spending"), {"year": "2024"})
        self.assertEqual([r.pk for r in resp.context["purchases"]], [old.pk])
        self.assertIn(2024, resp.context["years"])

        csv = self.client.get(reverse("spending"), {"format": "csv"}).content.decode()
        self.assertIn("Actual price", csv.splitlines()[0])
        self.assertEqual(len(csv.strip().splitlines()), 4)

        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("spending")).status_code, 302)

    def test_action_view(self):
        req = self.make("500")
        self.client.force_login(self.pi)
        self.client.post(reverse("request_action", args=[req.pk, "approve"]), {"approve-note": "ok"})
        req.refresh_from_db()
        self.assertEqual(req.status, Status.TO_ORDER)
        self.assertEqual(req.pi_comment, "ok")


def make_xlsx(path, rows):
    """Write a minimal one-sheet .xlsx ("Lab Purchases") using inline strings."""
    import zipfile
    from xml.sax.saxutils import escape

    def cell(ref, value):
        if value is None:
            return ""
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    sheet_rows = "".join(
        f'<row r="{i}">' + "".join(cell(f"{chr(65 + j)}{i}", v) for j, v in enumerate(row)) + "</row>"
        for i, row in enumerate(rows, start=1))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns}><sheets><sheet name="Lab Purchases" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{sheet_rows}</sheetData></worksheet>")


class ImportHistoryTests(TestCase):
    HEADER = ["Date", "Email Type", "PR/PO Number", "Description", "Supplier", "Quantity", "Unit Price (GBP)",
              "Total Amount (GBP)", "Tax (GBP)", "Cost Center", "Charge Account", "Grant Code", "Approved By", "Status",
              "Notes", "Source File"]

    def row(self, date, kind, number, desc, supplier, total, cost_centre=None, account=None, code=None):
        return [date, kind, number, desc, supplier, "1", total, total, None, cost_centre, account, code,
                "MARTIN, Derek", None, None, "x.eml"]

    def test_import(self):
        path = tempfile.mktemp(suffix=".xlsx")
        make_xlsx(path, [self.HEADER,
            # PR + matching PO (PO amount includes delivery)
            self.row("46070", "PR Approved", "PR-1", "[part-id:9] 3D printer", "ADDITIVE X LTD", "1374.17", "LJAA"),
            self.row("46071", "PO Approved", "PO-10", "C. TAMURA XML", "ADDITIVE X LTD", "1429.17", account="U.LJ.LJAA.AAAD.EKEJ.0000"),
            # PO with no PR details
            self.row("46072", "PO Approved", "PO-11", "C. TAMURA XML", "DELL CORPORATION LTD", "938.68", account="U.LJ.LJAG.MAMB.EKMZ.0000"),
            # PR with no PO
            self.row("46073", "PR Approved", "PR-2", "Dobot arm", "RAPID ELECTRONICS LTD", "2553.18", "LJAG"),
            # Amazon duplicate - skipped
            self.row("46073", "Amazon Order", None, "Gloves", "Amazon", "6.98"),
        ])
        try:
            call_command("import_history", path, "--dry-run", stdout=open(os.devnull, "w"))
            self.assertEqual(PurchaseRequest.objects.count(), 0)
            call_command("import_history", path, stdout=open(os.devnull, "w"))
            call_command("import_history", path, stdout=open(os.devnull, "w"))  # re-run adds nothing
        finally:
            os.remove(path)

        self.assertEqual(PurchaseRequest.objects.count(), 3)
        printer = PurchaseRequest.objects.get(import_key="PO-10")
        self.assertEqual((printer.item_name, printer.amount, printer.grant_code), ("3D printer", Decimal("1429.17"), "LJAA.AAAD"))
        self.assertEqual(printer.status, Status.RECEIVED)
        self.assertEqual(printer.purchase_date.isoformat(), "2026-02-18")  # PO date, not PR date
        self.assertEqual(printer.created_at.year, 2026)
        dell = PurchaseRequest.objects.get(import_key="PO-11")
        self.assertEqual((dell.item_name, dell.grant_code), ("Order from Dell Corporation Ltd", "LJAM/360"))
        dobot = PurchaseRequest.objects.get(order_reference="PR-2")
        self.assertTrue(dobot.unconfirmed_import)
        self.assertEqual(dobot.grant_code, "LJAG")

        buyer = User.objects.create_user("clara", "c@lab.ac.uk", "pw")
        buyer.groups.add(Group.objects.get(name="Purchaser"))
        self.assertIn("cancel", workflow.available_actions(dobot, buyer))
        self.assertNotIn("cancel", workflow.available_actions(dell, buyer))
        self.client.force_login(buyer)
        for req in (printer, dell, dobot):  # imported records have no purchaser / actor
            self.assertContains(self.client.get(req.get_absolute_url()), "Imported")
        self.assertContains(self.client.get(reverse("spending")), "Check: PR only")

    def test_lab_grant_codes(self):
        """The sheet's "Grant Code" column wins over the charge account, also for purchases already imported."""
        path = tempfile.mktemp(suffix=".xlsx")
        po = ["46072", "PO Approved", "PO-11", "x", "DELL CORPORATION LTD", "938.68"]
        pr = ["46073", "PR Approved", "PR-2", "Dobot arm", "RAPID ELECTRONICS LTD", "2553.18", "LJAG"]
        try:
            make_xlsx(path, [self.HEADER, self.row(*po, account="U.LJ.LJFV.EFYV.EKMZ.0000"), self.row(*pr)])
            call_command("import_history", path, stdout=open(os.devnull, "w"))
            self.assertEqual(PurchaseRequest.objects.get(import_key="PO-11").grant_code, "LJFV.EFYV")
            make_xlsx(path, [self.HEADER, self.row(*po, account="U.LJ.LJFV.EFYV.EKMZ.0000", code="LJFV-EFYV"),
                             self.row(*pr, code="LJAM/362"),
                             self.row("0.04", "PO Approved", "PO-12", "x", "ACME", "5.00", code="LJAM/360")])
            call_command("import_history", path, stdout=open(os.devnull, "w"))
        finally:
            os.remove(path)
        dell = PurchaseRequest.objects.get(import_key="PO-11")
        self.assertEqual(dell.grant_code, "LJFV-EFYV")
        self.assertTrue(dell.events.filter(action="grant code updated").exists())
        self.assertEqual(PurchaseRequest.objects.get(order_reference="PR-2").grant_code, "LJAM/362")
        self.assertFalse(PurchaseRequest.objects.filter(import_key="PO-12").exists())  # 1970 date - not imported

    def test_csv(self):
        """A CSV export of the sheet works too, with real date strings."""
        import csv
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.HEADER)
            w.writerow(self.row("2025-11-14 11:13:01", "PO Approved", "PO-11", "x", "DELL CORPORATION LTD", "837.09",
                                account="U.LJ.LJFV.EFYV.EKMZ.0000", code="LJFV-EFYV"))
        try:
            call_command("import_history", path, stdout=open(os.devnull, "w"))
        finally:
            os.remove(path)
        dell = PurchaseRequest.objects.get(import_key="PO-11")
        self.assertEqual((dell.grant_code, dell.purchase_date.isoformat()), ("LJFV-EFYV", "2025-11-14"))

    def test_known_fund_maps_to_lab_code(self):
        """A blank Grant Code with a charge account on a known fund gets the lab's code (LJAG.MAMB -> LJAM/360)."""
        path = tempfile.mktemp(suffix=".xlsx")
        make_xlsx(path, [self.HEADER, self.row("46072", "PO Approved", "PO-11", "x", "DELL CORPORATION LTD", "938.68",
                                               account="U.LJ.LJAG.MAMB.EKMZ.0000")])
        try:
            call_command("import_history", path, stdout=open(os.devnull, "w"))
        finally:
            os.remove(path)
        self.assertEqual(PurchaseRequest.objects.get(import_key="PO-11").grant_code, "LJAM/360")
