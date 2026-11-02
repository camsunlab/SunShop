"""
The lab's spending rules, in one place.

  Up to £200       buy at store with grant code / purchase form / reimbursement. No PI approval.
  £201 - £1,999    spec to Shijing (PI). Once approved -> purchase form.
  £2,000 - £4,999  spec + quote to Shijing, purchaser cc'd to start discussion. Once approved -> purchase form.
  £5,000+          spec + quote to Shijing. Once approved -> request to Derek, purchaser cc'd.

If the thresholds change, edit TIERS below - everything else reads from here.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Tier:
    number: int
    label: str
    range_label: str
    upper_limit: Decimal | None  # total must be <= this (None = no limit)
    needs_pi: bool
    needs_quote: bool
    cc_purchaser: bool  # purchaser is looped in on submission to start discussion
    needs_derek: bool
    steps: tuple[str, ...]


TIERS = (
    Tier(1, "Small", "Up to £200", Decimal("200.00"),
         needs_pi=False, needs_quote=False, cc_purchaser=False, needs_derek=False,
         steps=("Buy at the store with your grant code, ask the purchaser to order it, "
                "or buy it yourself and claim a reimbursement.",)),
    Tier(2, "Medium", "£201 – £1,999", Decimal("1999.99"),
         needs_pi=True, needs_quote=False, cc_purchaser=False, needs_derek=False,
         steps=("Shijing reviews your spec.",
                "Once approved, it goes to the purchaser to order.")),
    Tier(3, "Large", "£2,000 – £4,999", Decimal("4999.99"),
         needs_pi=True, needs_quote=True, cc_purchaser=True, needs_derek=False,
         steps=("Shijing reviews your spec and quote; the purchaser is looped in for discussion.",
                "Once approved, it goes to the purchaser to order.")),
    Tier(4, "Major", "£5,000 and over", None,
         needs_pi=True, needs_quote=True, cc_purchaser=False, needs_derek=True,
         steps=("Shijing reviews your spec and quote.",
                "Once approved, email Derek (purchaser cc'd) to request the purchase.",
                "Once Derek approves, the purchaser orders it.")),
)


def tier_for(total):
    total = Decimal(total or 0)
    for tier in TIERS:
        if tier.upper_limit is None or total <= tier.upper_limit:
            return tier
    return TIERS[-1]


def get_tier(number):
    return TIERS[number - 1]
