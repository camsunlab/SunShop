"""
Import the lab's purchase history from lab_purchases.xlsx (the sheet built from procurement emails),
or from a CSV export of its "Lab Purchases" sheet.

    python manage.py import_history history/lab_purchases.csv --dry-run   # preview, writes nothing
    python manage.py import_history history/lab_purchases.csv             # import

How the spreadsheet is read ("Lab Purchases" sheet):
  * Each PO row is one purchase: the amount actually ordered, with its charge account.
  * PR rows are the line items of a requisition. They're matched to a PO with the same supplier within a few
    days and a similar amount, and used for the PO's item description.
  * PR items with no matching PO are imported too, flagged "PR only - no PO found" for checking.
  * In a requisition with several lines, the sheet's Description column is shifted down one row (each line's
    description is on the row above, and the first one wraps round to the last). Supplier, quantity and prices
    are on the right row. unshift_descriptions() puts each description back with its own line.
  * "Amazon Order" rows are skipped - they're VAT-inclusive copies of Amazon Business PR items.
  * Amounts are kept as recorded (before VAT).
  * Grant code = the lab's own code from the "Grant Code" column (e.g. LJAM/360). Where that's blank, it falls
    back to cost centre + fund from the charge account, e.g. U.LJ.LJAG.MAMB.ELCO.0000 -> LJAG.MAMB, translated to
    the lab's code where we know it (FUND_CODES, e.g. LJAG.MAMB -> LJAM/360).

Safe to re-run: purchases already imported (same PO / PR) are skipped, but their grant code is updated if the
spreadsheet's "Grant Code" column now gives a different one. With --fix-descriptions, their item name and
spec are also rewritten from the sheet (used once to repair the shifted descriptions).
"""
import csv
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


DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")


def excel_date(value):
    """An Excel serial number (from .xlsx) or a date string (from .csv, e.g. 2025-11-14 11:13:01)."""
    value = (value or "").strip()
    try:
        return (datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(value))).date()
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise CommandError(f"Can't read the date {value!r}")


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


# Finance fund (cost centre.fund) -> the lab's grant code, for rows whose "Grant Code" cell is blank.
# Taken from the sheet, where every row with that fund that does have a code uses this one.
FUND_CODES = {
    "LJAG.MAMB": "LJAM/360",
    "LJAG.MADA": "LJAM/362",
    "LJAG.MAEB": "LJAM/315",
    "LJAG.MACB": "LJAM/365",
}


def lab_code(row):
    """The lab's own grant code from the "Grant Code" column, if filled in."""
    return (row.get("Grant Code") or "").strip() or FUND_CODES.get(grant_from_account(row.get("Charge Account")), "")


def unshift_descriptions(rows):
    """Give each line of a multi-line requisition its own description (see the module docstring)."""
    emails = defaultdict(list)
    for row in rows:
        if (row["Email Type"] or "").strip() == "PR Approved" and row["Description"]:
            emails[((row["PR/PO Number"] or "").strip(), row.get("Source File") or "")].append(row)
    for lines in emails.values():
        if len(lines) > 1:
            descriptions = [line["Description"] for line in lines]
            for line, description in zip(lines, descriptions[-1:] + descriptions[:-1]):
                line["Description"] = description
    return rows


def amounts_match(a, b):
    return abs(a - b) <= max(Decimal("2.00"), b * Decimal("0.10"))


class Command(BaseCommand):
    help = "Import purchase history from lab_purchases.xlsx into SunShop."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported without saving.")
        parser.add_argument("--fix-descriptions", action="store_true",
                            help="Also rewrite the item name and spec of purchases already imported.")

    def handle(self, path, dry_run=False, fix_descriptions=False, **options):
        rows = unshift_descriptions(self.load(path))

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
        pr_grants = {pr: FUND_CODES.get(code, code) for pr, code in pr_grants.items()}
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
        redescribe = []
        if fix_descriptions:
            current = {r.import_key: r for r in PurchaseRequest.objects.exclude(import_key="")}
            redescribe = [p for p in purchases if p["import_key"] in current
                          and (current[p["import_key"]].item_name, current[p["import_key"]].spec) != (p["item_name"], p["spec"])]
            for p in redescribe:
                self.stdout.write(f"    #{current[p['import_key']].pk} {p['vendor'][:24]:24} {current[p['import_key']].item_name[:45]!r}"
                                  f" -> {p['item_name'][:60]!r}")
            self.stdout.write(f"Already-imported purchases getting corrected descriptions: {len(redescribe)}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run - nothing saved. Run again without --dry-run to import."))
            return
        with transaction.atomic():
            user = self.history_user()
            for p in new:
                self.save(p, user)
            for p in regrant:
                self.update_grant(p, existing[p["import_key"]])
            for p in redescribe:
                self.update_description(p)
        self.stdout.write(self.style.SUCCESS(f"Imported {len(new)} purchases; updated the grant code of {len(regrant)}."))

    def load(self, path):
        try:
            if path.lower().endswith(".csv"):
                with open(path, encoding="utf-8-sig", newline="") as f:
                    return list(csv.DictReader(f))
            sheets = read(path)
        except (OSError, KeyError, UnicodeDecodeError) as e:
            raise CommandError(f"Couldn't read {path}: {e}")
        if SHEET not in sheets:
            raise CommandError(f"No '{SHEET}' sheet in {path}")
        return records(sheets[SHEET])

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
        if not code and sibling_grant in FUND_CODES.values():
            code = sibling_grant
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

    def update_description(self, p):
        req = PurchaseRequest.objects.get(import_key=p["import_key"])
        old = req.item_name
        req.item_name, req.spec = p["item_name"], p["spec"]
        req.save(update_fields=["item_name", "spec"])
        Event.objects.create(request=req, actor=None, action="corrected",
                             message=f"Description corrected (was \"{old}\"): the spreadsheet had the item descriptions "
                                     "of this requisition shifted by one line.")

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
