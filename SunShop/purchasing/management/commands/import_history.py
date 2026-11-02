"""
Import the lab's purchase history from lab_purchases.xlsx (the sheet built from procurement emails).

    python manage.py import_history lab_purchases.xlsx --dry-run   # preview, writes nothing
    python manage.py import_history lab_purchases.xlsx             # import

How the spreadsheet is read ("Lab Purchases" sheet):
  * Each PO row is one purchase: the amount actually ordered, with its charge account.
  * PR rows are the line items of a requisition. They're matched to a PO with the same supplier within a few
    days and a similar amount, and used for the PO's item description.
  * PR items with no matching PO are imported too, flagged "PR only - no PO found" for checking.
  * "Amazon Order" rows are skipped - they're VAT-inclusive copies of Amazon Business PR items.
  * Amounts are kept as recorded (before VAT).
  * Grant code = the lab's own code from the "Grant Code" column (e.g. LJAM/360). Where that's blank, it falls
    back to cost centre + fund from the charge account, e.g. U.LJ.LJAG.MAMB.ELCO.0000 -> LJAG.MAMB.

Safe to re-run: purchases already imported (same PO / PR) are skipped, but their grant code is updated if the
spreadsheet's "Grant Code" column now gives a different one.
"""
import datetime
import html
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from purchasing.models import Event, Method, PurchaseRequest, Status
from purchasing.xlsx import read, records

SHEET = "Lab Purchases"
MATCH_DAYS = 3  # a PR and its PO are approved within this many days of each other
HISTORY_USERNAME = "history"


def excel_date(value):
    return (datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(value))).date()


def money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def clean(text):
    text = html.unescape(text or "").replace("_x000D_", " ")
    text = re.sub(r"^\[part-id:\d+\]\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def grant_from_account(account):
    """U.LJ.LJAG.MAMB.ELCO.0000 -> LJAG.MAMB"""
    parts = (account or "").split(".")
    return ".".join(parts[2:4]) if len(parts) >= 4 else ""


def lab_code(row):
    """The lab's own grant code from the "Grant Code" column, if filled in."""
    return (row.get("Grant Code") or "").strip()


def amounts_match(a, b):
    return abs(a - b) <= max(Decimal("2.00"), b * Decimal("0.10"))


class Command(BaseCommand):
    help = "Import purchase history from lab_purchases.xlsx into SunShop."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported without saving.")

    def handle(self, path, dry_run=False, **options):
        try:
            sheets = read(path)
        except (OSError, KeyError) as e:
            raise CommandError(f"Couldn't read {path}: {e}")
        if SHEET not in sheets:
            raise CommandError(f"No '{SHEET}' sheet in {path}")
        rows = records(sheets[SHEET])

        pos, pr_lines, skipped = [], [], defaultdict(int)
        for row in rows:
            kind = (row["Email Type"] or "").strip()
            number = (row["PR/PO Number"] or "").strip()
            if kind == "PO Approved" and number not in ("", "PO-"):
                pos.append(row)
            elif kind == "PR Approved" and row["Description"] and money(row["Total Amount (GBP)"]) is not None:
                pr_lines.append(row)
            elif kind == "Amazon Order":
                skipped["Amazon Order rows (duplicates of Amazon Business items)"] += 1
            elif kind == "PR Approved":
                skipped["PR approvals with no item details"] += 1
            else:
                skipped[f"{kind or 'blank'} rows with no PO number"] += 1

        # Group PR lines into one requisition per (PR number, supplier) - that's what becomes a PO.
        groups = defaultdict(list)
        for line in pr_lines:
            groups[(line["PR/PO Number"].strip(), (line["Supplier"] or "").strip())].append(line)
        pr_groups = [{"pr": pr, "supplier": supplier, "lines": lines, "date": excel_date(lines[0]["Date"]),
                      "total": sum(money(l["Total Amount (GBP)"]) for l in lines), "po": None}
                     for (pr, supplier), lines in groups.items()]

        purchases = []
        for po in pos:
            date, supplier, amount = excel_date(po["Date"]), (po["Supplier"] or "").strip(), money(po["Total Amount (GBP)"])
            candidates = [g for g in pr_groups if g["po"] is None and g["supplier"] == supplier
                          and abs((g["date"] - date).days) <= MATCH_DAYS
                          and (amount is None or amounts_match(g["total"], amount))]
            match = min(candidates, key=lambda g: (abs(g["total"] - (amount or 0)), abs((g["date"] - date).days)), default=None)
            if match:
                match["po"] = po
            purchases.append(self.from_po(po, date, supplier, amount, match))

        # PR items with no PO. Use the fund of a sibling PO from the same requisition if there is one.
        pr_grants = {g["pr"]: lab_code(g["po"]) or grant_from_account(g["po"]["Charge Account"])
                     for g in pr_groups if g["po"]}
        for g in pr_groups:
            if g["po"] is None and g["total"] > 0:
                purchases.append(self.from_pr(g, pr_grants.get(g["pr"])))

        existing = dict(PurchaseRequest.objects.exclude(import_key="").values_list("import_key", "grant_code"))
        new = [p for p in purchases if p["import_key"] not in existing]
        undated = [p for p in new if p["date"].year < 2000]
        if undated:
            # Dates that come out as 1970 mean the sheet's Date column has been lost (saved as 0 / epoch).
            self.stdout.write(self.style.WARNING(
                f"Not importing {len(undated)} new purchases dated before 2000 - check the spreadsheet's Date column: "
                + ", ".join(p["import_key"] for p in undated[:10])))
            new = [p for p in new if p not in undated]
        regrant = [p for p in purchases if p["import_key"] in existing and p["lab_code"]
                   and existing[p["import_key"]] != p["grant_code"]]
        self.report(purchases, new, skipped, len(existing.keys() & {p["import_key"] for p in purchases}))
        for p in regrant:
            self.stdout.write(f"    grant code {existing[p['import_key']] or '(none)'} -> {p['grant_code']}  "
                              f"{p['import_key']}  {p['vendor'][:28]}")
        self.stdout.write(f"Already-imported purchases getting the lab's grant code: {len(regrant)}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run - nothing saved. Run again without --dry-run to import."))
            return
        with transaction.atomic():
            user = self.history_user()
            for p in new:
                self.save(p, user)
            for p in regrant:
                self.update_grant(p, existing[p["import_key"]])
        self.stdout.write(self.style.SUCCESS(f"Imported {len(new)} purchases; updated the grant code of {len(regrant)}."))

    # ------------------------------------------------------------ building records

    def from_po(self, po, date, supplier, amount, match):
        number = po["PR/PO Number"].strip()
        notes = [f"Imported from lab_purchases.xlsx ({po['Source File'] or 'PO approval email'})."]
        if po["Approved By"]:
            notes.append(f"Approved by {po['Approved By']} (procurement).")
        if amount is None:
            notes.append("The amount wasn't recorded in the spreadsheet.")
        notes.append("Amounts are as recorded in procurement (before VAT).")
        if match:
            item_name, spec = self.describe(match["lines"])
            notes.insert(1, f"Requisition {match['pr']}.")
        else:
            item_name = f"Order from {supplier.title() if supplier.isupper() else supplier}"
            spec = "Item details weren't recorded in the PO email."
        return {
            "import_key": number, "date": date, "item_name": item_name, "spec": spec, "vendor": supplier,
            "amount": amount, "grant_code": lab_code(po) or grant_from_account(po["Charge Account"]),
            "lab_code": lab_code(po), "order_reference": number,
            "notes": " ".join(notes), "status": Status.RECEIVED,
        }

    def from_pr(self, group, sibling_grant):
        item_name, spec = self.describe(group["lines"])
        cost_centre = (group["lines"][0]["Cost Center"] or "").strip()
        code = next((lab_code(l) for l in group["lines"] if lab_code(l)), "")
        grant = code or sibling_grant or cost_centre
        notes = [f"PR only – no PO found. Imported from lab_purchases.xlsx (requisition {group['pr']}); "
                 "check whether this was actually ordered."]
        approver = group["lines"][0]["Approved By"]
        if approver:
            notes.append(f"Approved by {approver} (procurement).")
        if not code and not sibling_grant:
            notes.append(f"Only the cost centre ({cost_centre}) was recorded, not the fund.")
        notes.append("Amounts are as recorded in procurement (before VAT).")
        return {
            "import_key": f"{group['pr']} {group['supplier']}", "date": group["date"], "item_name": item_name,
            "spec": spec, "vendor": group["supplier"], "amount": group["total"], "grant_code": grant,
            "lab_code": code,
            "order_reference": group["pr"], "notes": " ".join(notes), "status": Status.RECEIVED,
        }

    def describe(self, lines):
        names = [clean(l["Description"]) for l in lines]
        item_name = names[0][:150] + (f" + {len(names) - 1} more" if len(names) > 1 else "")
        spec = "\n".join(
            f"{l['Quantity'] or 1} × {name} – £{money(l['Total Amount (GBP)']):,.2f}" for l, name in zip(lines, names))
        return item_name[:200], spec

    # ------------------------------------------------------------ saving

    def history_user(self):
        user, created = get_user_model().objects.get_or_create(
            username=HISTORY_USERNAME, defaults={"first_name": "Historical", "last_name": "import", "is_active": False})
        if created:
            user.set_unusable_password()
            user.save()
        return user

    def save(self, p, user):
        amount = p["amount"] or Decimal("0.00")
        req = PurchaseRequest(
            requester=user, item_name=p["item_name"], spec=p["spec"], vendor=p["vendor"], quantity=1,
            unit_price=amount, actual_price=p["amount"], grant_code=p["grant_code"], method=Method.PURCHASE_FORM,
            notes=p["notes"], status=p["status"], ordered_at=p["date"], order_reference=p["order_reference"],
            import_key=p["import_key"],
        )
        req.save()
        # created_at is set automatically on save; backdate it to the original purchase date.
        created = timezone.make_aware(datetime.datetime.combine(p["date"], datetime.time(12)))
        PurchaseRequest.objects.filter(pk=req.pk).update(created_at=created)
        Event.objects.create(request=req, actor=None, action="imported", message=p["notes"])
        Event.objects.filter(request=req).update(created_at=created)

    def update_grant(self, p, old):
        req = PurchaseRequest.objects.get(import_key=p["import_key"])
        req.grant_code = p["grant_code"]
        req.save(update_fields=["grant_code"])
        Event.objects.create(request=req, actor=None, action="grant code updated",
                             message=f"Grant code changed from {old or '(none)'} to {p['grant_code']} "
                                     "(the lab's code from lab_purchases.xlsx).")

    def report(self, purchases, new, skipped, already):
        po = [p for p in purchases if not p["import_key"].startswith("PR-")]
        pr = [p for p in purchases if p["import_key"].startswith("PR-")]
        total = lambda ps: sum((p["amount"] or 0) for p in ps)
        described = sum(1 for p in po if not p["item_name"].startswith("Order from "))
        self.stdout.write(f"POs: {len(po)} (£{total(po):,.2f}); {described} matched to PR item details")
        self.stdout.write(f"PR items with no PO (flagged for checking): {len(pr)} (£{total(pr):,.2f})")
        for p in sorted(pr, key=lambda p: -(p["amount"] or 0))[:10]:
            self.stdout.write(f"    {p['date']}  £{p['amount']:>9,.2f}  {p['vendor'][:28]:28}  {p['item_name'][:50]}")
        missing = [p for p in po if p["amount"] is None]
        if missing:
            self.stdout.write(f"POs with no amount recorded: {', '.join(p['import_key'] + ' ' + p['vendor'] for p in missing)}")
        for reason, n in skipped.items():
            self.stdout.write(f"Skipped {n} {reason}")
        by_year = defaultdict(Decimal)
        by_grant = defaultdict(Decimal)
        for p in purchases:
            by_year[p["date"].year] += p["amount"] or 0
            by_grant[p["grant_code"] or "(none)"] += p["amount"] or 0
        self.stdout.write("By year: " + ", ".join(f"{y}: £{v:,.2f}" for y, v in sorted(by_year.items())))
        self.stdout.write("By grant code: " + ", ".join(f"{g}: £{v:,.2f}" for g, v in sorted(by_grant.items(), key=lambda kv: -kv[1])))
        if already:
            self.stdout.write(f"Already imported (will skip): {already}")
        self.stdout.write(f"New purchases to import: {len(new)}")
