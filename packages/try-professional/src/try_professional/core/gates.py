"""Gate rules: which gates apply, computed from flags and never from the plan.

Flags are a union of what the owner declared, what the plan declared, and these
deterministic checks. A model can add a flag but cannot remove one.
"""

from __future__ import annotations

import re
from typing import Any

from ..org import Org
from .artifacts import Flags

_LICENSE_PATH = re.compile(r"(^|/)(licen[cs]e|copying|notice)(\.[a-z]+)?$", re.IGNORECASE)
_PERSONAL_DATA = re.compile(
    r"\b("
    r"e-?mail|phone|passport|date[_ ]of[_ ]birth|birth[_ ]?date|national[_ ]id|ssn|home[_ ]address|"
    r"personal[_ ]data|customer[_ ]data|credit[_ ]card|"
    r"data pribadi|nomor telepon|alamat rumah|ktp|"  # Indonesian
    r"datos personales|correo electr[oó]nico|tel[eé]fono"  # Spanish
    r")\b|个人信息|身份证|手机号|البيانات الشخصية|رقم الهاتف",
    re.IGNORECASE,
)


def deterministic_flags(output_type: str, payload: dict[str, Any]) -> Flags:
    """Flags that follow from the output itself, whatever the model declared."""
    public_facing = output_type == "PublicContent"
    touches_license = False
    if output_type == "CodeChange":
        touches_license = any(_LICENSE_PATH.search(str(f.get("path", ""))) for f in payload.get("files", []))
    touches_personal_data = bool(_PERSONAL_DATA.search(_all_text(payload)))
    return Flags(
        public_facing=public_facing,
        touches_license=touches_license,
        touches_personal_data=touches_personal_data,
    )


def _all_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_all_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_all_text(v) for v in value)
    return ""


def triggered_gates(org: Org, flags: Flags) -> list[str]:
    active = set(flags.active())
    return [name for name, gate in org.gates.items() if active.intersection(gate.triggers)]
