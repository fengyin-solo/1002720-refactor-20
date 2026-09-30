"""危险品申报的统一判定口径。

提交申报、审核通过、放行确认都只调用这里，避免同一票申报在不同动作中算出
不同的类别、联合国编号或隔离要求。
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

CLASS_PATTERN = re.compile(r"^(?:[1-9](?:\.[1-6])?)$")
UN_NUMBER_PATTERN = re.compile(r"^(?:UN[-\s]?)?(\d{4})$", re.IGNORECASE)

CLASS_ALIASES = {
    "一类": "1",
    "二类": "2",
    "三类": "3",
    "四类": "4",
    "五类": "5",
    "六类": "6",
    "七类": "7",
    "八类": "8",
    "九类": "9",
    "1类": "1",
    "2类": "2",
    "3类": "3",
    "4类": "4",
    "5类": "5",
    "6类": "6",
    "7类": "7",
    "8类": "8",
    "9类": "9",
}

SEGREGATION_BY_CLASS = {
    "1": "爆炸品隔离",
    "2": "气体隔离",
    "3": "易燃液体隔离",
    "4": "易燃固体隔离",
    "5": "氧化剂隔离",
    "6": "毒害品隔离",
    "7": "放射性隔离",
    "8": "腐蚀品隔离",
    "9": "杂项危险品隔离",
}

DEFAULT_PACKING_GROUP = "未指定"
DEFAULT_STOWAGE_REQUIREMENT = "常规积载"
PENDING_SEGREGATION_REQUIREMENT = "待人工核定"


@dataclass(frozen=True)
class DeclarationDecision:
    """一次判定生成的唯一结论，后续动作直接复用。"""

    valid: bool
    hazardous_class: str
    un_number: str
    packing_group: str
    stowage_requirement: str
    segregation_requirement: str
    errors: tuple[str, ...]
    rule_version: str

    @property
    def conclusion(self) -> str:
        return "申报通过" if self.valid else "申报驳回"

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "conclusion": self.conclusion,
            "hazardous_class": self.hazardous_class,
            "un_number": self.un_number,
            "packing_group": self.packing_group,
            "stowage_requirement": self.stowage_requirement,
            "segregation_requirement": self.segregation_requirement,
            "errors": list(self.errors),
            "rule_version": self.rule_version,
        }


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def default_entry_fields() -> dict[str, str]:
    return {
        "联合国编号": "",
        "包装等级": DEFAULT_PACKING_GROUP,
        "积载要求": DEFAULT_STOWAGE_REQUIREMENT,
        "隔离要求": PENDING_SEGREGATION_REQUIREMENT,
    }


def normalize_class(value: Any) -> tuple[str, bool]:
    raw = text(value)
    normalized = CLASS_ALIASES.get(raw, raw)
    if CLASS_PATTERN.fullmatch(normalized):
        return normalized, True
    return raw, False


def normalize_un_number(value: Any) -> tuple[str, bool]:
    raw = text(value)
    matched = UN_NUMBER_PATTERN.fullmatch(raw)
    if not matched:
        return raw, False
    return f"UN{matched.group(1)}", True


def evaluate_declaration(values: dict[str, Any]) -> DeclarationDecision:
    """按新口径校验类别、联合国编号并计算隔离要求。"""

    errors: list[str] = []
    hazardous_class, class_ok = normalize_class(values.get("危品类别"))
    un_number, un_ok = normalize_un_number(values.get("联合国编号"))

    if not text(values.get("危品类别")):
        errors.append("危品类别不能为空")
    elif not class_ok:
        errors.append("危品类别必须为 1-9 类（可含小类，如 1.1）")

    if not text(values.get("联合国编号")):
        errors.append("联合国编号不能为空")
    elif not un_ok:
        errors.append("联合国编号必须为 UN 加四位数字，如 UN1203")

    valid = not errors
    primary_class = hazardous_class.split(".", 1)[0] if class_ok else ""
    segregation = (
        SEGREGATION_BY_CLASS.get(primary_class, PENDING_SEGREGATION_REQUIREMENT)
        if valid
        else PENDING_SEGREGATION_REQUIREMENT
    )

    return DeclarationDecision(
        valid=valid,
        hazardous_class=hazardous_class,
        un_number=un_number,
        packing_group=text(values.get("包装等级")) or DEFAULT_PACKING_GROUP,
        stowage_requirement=text(values.get("积载要求")) or DEFAULT_STOWAGE_REQUIREMENT,
        segregation_requirement=segregation,
        errors=tuple(errors),
        rule_version="dangerous-v1",
    )


def sync_entry_fields(entry: dict[str, Any], decision: DeclarationDecision) -> None:
    """把结论同步到对外字段，不重新生成或替换首次判定快照。"""

    entry["危品类别"] = decision.hazardous_class
    entry["联合国编号"] = decision.un_number
    entry["包装等级"] = decision.packing_group
    entry["积载要求"] = decision.stowage_requirement
    entry["隔离要求"] = decision.segregation_requirement


def apply_decision(entry: dict[str, Any], decision: DeclarationDecision) -> None:
    """把唯一结论写回申报；对外字段仍沿用原有中文名。"""

    sync_entry_fields(entry, decision)
    entry["_decision"] = decision.as_snapshot()


def stored_decision(entry: dict[str, Any]) -> DeclarationDecision | None:
    snapshot = entry.get("_decision")
    if not isinstance(snapshot, dict):
        return None

    errors_data = snapshot.get("errors", ())
    errors = tuple(str(item) for item in errors_data) if isinstance(errors_data, list) else ()
    return DeclarationDecision(
        valid=bool(snapshot.get("valid")),
        hazardous_class=str(snapshot.get("hazardous_class", "")),
        un_number=str(snapshot.get("un_number", "")),
        packing_group=str(snapshot.get("packing_group", DEFAULT_PACKING_GROUP)),
        stowage_requirement=str(snapshot.get("stowage_requirement", DEFAULT_STOWAGE_REQUIREMENT)),
        segregation_requirement=str(
            snapshot.get("segregation_requirement", PENDING_SEGREGATION_REQUIREMENT)
        ),
        errors=errors,
        rule_version=str(snapshot.get("rule_version", "")),
    )
