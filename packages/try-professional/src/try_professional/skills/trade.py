"""Whether a skill is tied to a platform or industry this company isn't in.

The judge reads the business too, but this runs first and costs nothing, and it works with no model at all. It
only looks at skills that name their platform or industry in their own ID or name, or in a description that names
exactly one platform. A skill that mentions several is general. A company with no business context filters nothing,
and a person's approval always overrides this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..org import Org
from .catalog import SkillRecord

_RETAIL = ("store", "shop", "retail", "ecommerce", "e commerce", "merchant", "marketplace", "toko", "dropshipping")


@dataclass(frozen=True)
class Tie:
    label: str
    names: tuple[str, ...]
    # The company fits if its context has one of these. Social platforms have none, and fit only when named.
    fits: tuple[str, ...] = ()


_COMMERCE = tuple(
    Tie(label, names, (*names, *_RETAIL))
    for label, names in (
        ("Shopify", ("shopify",)),
        ("WooCommerce", ("woocommerce",)),
        ("BigCommerce", ("bigcommerce",)),
        ("Magento", ("magento",)),
        ("TikTok Shop", ("tiktok shop",)),
        ("Amazon selling", ("amazon fba", "amazon seller", "amazon listing")),
        ("Etsy", ("etsy",)),
        ("eBay", ("ebay",)),
        ("online retail", ("ecommerce", "e commerce", "dropshipping", "dtc")),
    )
)
_SOCIAL = tuple(
    Tie(label, names)
    for label, names in (
        ("Instagram", ("instagram",)),
        ("TikTok", ("tiktok",)),
        ("LinkedIn", ("linkedin",)),
        ("YouTube", ("youtube",)),
        ("Facebook", ("facebook",)),
        ("Pinterest", ("pinterest",)),
        ("X", ("twitter",)),
        ("Reddit", ("reddit",)),
        ("Snapchat", ("snapchat",)),
    )
)
_INDUSTRIES = (
    Tie("mobile apps", ("app store", "google play", "aso"), ("app", "apps", "mobile", "aso")),
    Tie("SaaS", ("saas",), ("saas", "software", "subscription")),
    Tie("real estate", ("real estate", "realtor"), ("real estate", "property", "realtor")),
    Tie("restaurants", ("restaurant",), ("restaurant", "cafe", "catering", "food")),
    Tie("crypto", ("crypto", "web3", "blockchain"), ("crypto", "web3", "blockchain")),
    Tie("fintech", ("fintech",), ("fintech", "finance", "bank", "payments", "invoicing")),
    Tie("law firms", ("law firm",), ("law firm", "legal", "attorney")),
    Tie("hotels", ("hotel", "hospitality"), ("hotel", "hospitality", "resort", "travel")),
)
_ALL = (*_COMMERCE, *_SOCIAL, *_INDUSTRIES)


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _has(words: str, phrase: str) -> bool:
    return f" {phrase} " in words


def _named(words: str, ties: tuple[Tie, ...]) -> list[Tie]:
    return [tie for tie in ties if any(_has(words, name) for name in tie.names)]


def ties_of(record: SkillRecord) -> list[Tie]:
    """What the skill is tied to. TikTok Shop counts as itself, and not also as TikTok."""
    named = _named(_words(f"{record.skill_id} {record.name}"), _ALL)
    if not named:
        described = _named(_words(record.description), _ALL)
        named = described if len(described) == 1 else []
    labels = {tie.label for tie in named}
    return [tie for tie in named if not (tie.label == "TikTok" and "TikTok Shop" in labels)]


def off_trade_reason(record: SkillRecord, org: Org) -> str | None:
    """Why this skill is for another trade than this company's, or None."""
    business = org.business
    words = _words(business.text)
    if not words.strip():
        return None
    ties = ties_of(record)
    if not ties:
        return None
    named_social = _named(words, _SOCIAL)
    wrong: list[str] = []
    for tie in ties:
        if tie in _SOCIAL:
            # With no channels known, a social skill might still fit, so only a stated choice rules it out.
            if tie in named_social or not (named_social or business.channels):
                return None
        elif any(_has(words, word) for word in tie.fits):
            return None
        wrong.append(tie.label)
    return f"tied to {', '.join(wrong)}, which this business doesn't use"
