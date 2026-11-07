"""What the Org Architect may say about a company.

This shape is the main safety decision here. Generated text becomes system prompts, and org.yaml can
name endpoints and keys, so the model gets a narrow form to fill in. It has no field for tools,
gates, budgets, escalation, or model settings, and anything extra is rejected.
"""

from __future__ import annotations

import html
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
Line = Annotated[str, StringConstraints(min_length=1, max_length=160)]
Phrase = Annotated[str, StringConstraints(min_length=1, max_length=90)]
WorkOutput = Literal["PublicContent", "ResearchReport", "TaskReport", "CodeChange"]
_LANGUAGE = re.compile(r"^(auto|[a-z]{2,3}(-[A-Za-z0-9]{2,8})*)$")
# A region only changes the written language for these.
_KEEP_REGION = frozenset({"pt", "zh"})
# How a description would name a language. Only used to confirm that it asked for one.
_LANGUAGE_NAMES = {
    "ar": ("arabic", "العربية"),
    "de": ("german", "deutsch"),
    "en": ("english",),
    "es": ("spanish", "español", "espanol"),
    "fr": ("french", "français", "francais"),
    "hi": ("hindi", "हिन्दी"),
    "id": ("indonesian", "bahasa indonesia"),
    "it": ("italian", "italiano"),
    "ja": ("japanese", "日本語"),
    "ko": ("korean", "한국어"),
    "ms": ("malay", "bahasa melayu"),
    "nl": ("dutch", "nederlands"),
    "pt": ("portuguese", "português", "portugues"),
    "ru": ("russian", "русский", "русском"),
    "th": ("thai", "ภาษาไทย"),
    "tr": ("turkish", "türkçe"),
    "vi": ("vietnamese", "tiếng việt"),
    "zh": ("chinese", "mandarin", "中文"),
}
_ASKS_BEFORE = r"(?:\b(?:in|en|em|auf|dalam|only|use|using|menggunakan)\s+|用|使用)"
_ASKS_AFTER = r"[- ](?:language|speaking|speakers?|only|content)\b"
# These name the language itself, so saying them at all is asking for it.
_ALWAYS_EXPLICIT = ("bahasa indonesia", "bahasa melayu", "tiếng việt", "ภาษาไทย")


def normalize_language(value: Any) -> str:
    """One code for one language: id-ID and id_id are both id. Keeps a script, and a region only where it matters."""
    text = str(value or "auto").strip().replace("_", "-")
    if text.lower() == "auto":
        return "auto"
    primary, *rest = text.split("-")
    primary = primary.lower()
    parts = [primary]
    for part in rest:
        if len(part) == 4 and part.isalpha():
            parts.append(part.capitalize())
        elif primary in _KEEP_REGION and len(part) in (2, 3):
            parts.append(part.upper())
    return "-".join(parts)


def asked_for_language(text: str) -> str | None:
    """The language this text explicitly asks for, such as "all content in Indonesian", or None.

    A place or a trade doesn't count: "a Spanish restaurant" and "UMKM Indonesia" ask for nothing.
    """
    lowered = text.lower()
    for code, names in _LANGUAGE_NAMES.items():
        for name in names:
            escaped = re.escape(name)
            if name in _ALWAYS_EXPLICIT and name in lowered:
                return code
            # Latin letters only: "in german" isn't "in germany", while Chinese has no spaces between words.
            if re.search(rf"{_ASKS_BEFORE}{escaped}(?![a-zà-ÿ])", lowered) or re.search(
                rf"(?<!\w){escaped}{_ASKS_AFTER}", lowered
            ):
                return code
    return None


def settle_language(chosen: str, text: str) -> str:
    """auto, so each command follows its own language, unless the business asked for one.

    A model tends to fill this in anyway. Its answer stands only if the text names that language, and a language
    missing from the names above can't be checked, so its answer stands there too.
    """
    asked = asked_for_language(text)
    if asked:
        return asked
    code = normalize_language(chosen)
    primary = code.split("-")[0]
    if code == "auto" or primary not in _LANGUAGE_NAMES:
        return code
    lowered = text.lower()
    return code if any(name in lowered for name in _LANGUAGE_NAMES[primary]) else "auto"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean_name(value: Any) -> Any:
    """Models sometimes write &amp; in a name, and it would be shown just like that."""
    return " ".join(html.unescape(value).split()) if isinstance(value, str) else value


class ProposedRole(_Strict):
    id: Identifier
    name: str = Field(min_length=1, max_length=60)
    _name = field_validator("name", mode="before")(_clean_name)
    kind: Literal["head", "worker", "reviewer"]
    mission: str = Field(min_length=1, max_length=200)
    responsibilities: list[Line] = Field(min_length=1, max_length=6)
    skill_needs: list[Phrase] = Field(
        min_length=1, max_length=6, description="Short English phrases naming capabilities this role's skills provide"
    )
    skill_categories: list[Identifier] = Field(min_length=1, max_length=5)
    outputs: list[WorkOutput] = Field(min_length=1, max_length=3)
    instructions: str = Field(
        min_length=1, max_length=400, description="How this role does good work. One to three sentences"
    )


class ProposedDepartment(_Strict):
    id: Identifier
    name: str = Field(min_length=1, max_length=60)
    _name = field_validator("name", mode="before")(_clean_name)
    from_template: Identifier | None = Field(None, description="The template department to start from, if any")
    keep_roles: list[Identifier] = Field(
        [], description="Roles to keep from that department. Leave empty, with no new roles, to keep them all"
    )
    roles: list[ProposedRole] = Field([], max_length=8, description="New roles")

    @model_validator(mode="after")
    def _has_roles(self) -> ProposedDepartment:
        if not self.from_template and not self.roles:
            raise ValueError(f"department '{self.id}' needs roles, or a from_template")
        if self.keep_roles and not self.from_template:
            raise ValueError(f"department '{self.id}' has keep_roles, but no from_template")
        return self


class ProposedBusiness(_Strict):
    industry: str = Field("", max_length=80, description="In English, such as physiotherapy clinic")
    products: list[Phrase] = Field([], max_length=6, description="What it sells or makes, in English")
    channels: list[Phrase] = Field(
        [], max_length=8, description="Only platforms it really uses to sell or publish, such as YouTube or Shopify"
    )


class OrgProposal(_Strict):
    company_name: str = Field(min_length=1, max_length=80)
    rationale: str = Field("", max_length=400, description="Why this structure suits the business, in one sentence")
    working_language: str = Field(
        "auto", description="auto, or one BCP 47 code if the business explicitly asks for one language"
    )
    business: ProposedBusiness = Field(default_factory=ProposedBusiness)
    departments: list[ProposedDepartment] = Field(min_length=1, max_length=12)

    @field_validator("working_language", mode="before")
    @classmethod
    def _one_code(cls, value: Any) -> str:
        return normalize_language(value)

    @model_validator(mode="after")
    def _language_is_a_tag(self) -> OrgProposal:
        if not _LANGUAGE.match(self.working_language):
            raise ValueError("working_language must be 'auto' or a BCP 47 tag")
        return self
