"""危险品申报业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.dangerous_policy import (
    apply_decision,
    default_entry_fields,
    evaluate_declaration,
    stored_decision,
    sync_entry_fields,
    text,
)
from app.store import store

MODULE = "dangerous"
REQUIRED_FIELDS = ["申报编号", "箱号", "危品类别"]
DETAIL_FIELDS = ["危品类别", "联合国编号", "包装等级", "积载要求", "隔离要求"]
STATUS_ORDER = ["待申报", "已申报", "海关审核", "已放行"]
ACTION_RULES = {"提交申报": "已申报", "审核通过": "海关审核", "放行确认": "已放行"}
MANAGED_MARK = "_managed_rule_version"
RULE_VERSION = "dangerous-v1"


def public_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    result = {key: value for key, value in entry.items() if not key.startswith("_")}
    decision = stored_decision(entry)
    if decision is not None:
        result.update({
            "危品类别": decision.hazardous_class,
            "联合国编号": decision.un_number,
            "包装等级": decision.packing_group,
            "积载要求": decision.stowage_requirement,
            "隔离要求": decision.segregation_requirement,
        })
    return result


class DangerousService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("申报编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows: list[dict[str, Any]] = []
        for row in rows[start:start + size]:
            public = public_entry(row)
            if public is not None:
                page_rows.append(public)
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return public_entry(store.find(MODULE, entry_id))

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not text(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: text(values.get(field)) for field in REQUIRED_FIELDS})
        entry.update(default_entry_fields())
        for field in DETAIL_FIELDS:
            value = text(values.get(field))
            if value:
                entry[field] = value
        entry["status"] = STATUS_ORDER[0]
        entry["申报状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry[MANAGED_MARK] = RULE_VERSION
        rows.append(entry)
        return public_entry(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, bool]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"危险品 {entry_id} 不存在或已归档", False
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于危险品申报可执行范围", False

        target = ACTION_RULES[action]
        target_index = STATUS_ORDER.index(target)
        current_status = str(entry.get("status") or "")

        is_managed = entry.get(MANAGED_MARK) == RULE_VERSION
        decision = stored_decision(entry) if is_managed else None
        if is_managed and action != "提交申报":
            if decision is None:
                return None, "危险品尚未提交申报，请先按统一口径生成申报结论", False
            if not decision.valid:
                return None, "首次申报结论为申报驳回，不能继续审核或放行", False
        if is_managed and action == "提交申报" and decision is not None and not decision.valid:
            sync_entry_fields(entry, decision)
            return (
                public_entry(entry),
                f"重复申报只认首次结论：{decision.conclusion}",
                False,
            )

        if current_status == target:
            if decision is not None:
                sync_entry_fields(entry, decision)
            suffix = f"，沿用首次结论：{decision.conclusion}" if decision else ""
            return public_entry(entry), f"危险品已{action}{suffix}", True

        if current_status not in STATUS_ORDER or STATUS_ORDER.index(current_status) != target_index - 1:
            return None, f"危险品当前为「{current_status or '未知状态'}」，不能执行{action}", False

        if not is_managed:
            self._transition(entry, target, abnormal=bool(entry.get("abnormal")))
            return public_entry(entry), f"危险品已{action}", True

        if action == "提交申报":
            duplicate = self._find_submitted_duplicate(entry)
            if duplicate is not None:
                duplicate_decision = stored_decision(duplicate)
                conclusion = duplicate_decision.conclusion if duplicate_decision else "已提交"
                return None, f"申报编号已提交，重复申报只认首次结论：{conclusion}", False

            decision = evaluate_declaration(entry)
            apply_decision(entry, decision)
            if not decision.valid:
                entry["abnormal"] = True
                return public_entry(entry), f"危险品申报未通过：{'；'.join(decision.errors)}", False
            self._transition(entry, target, abnormal=False)
            return public_entry(entry), f"危险品已{action}，结论：{decision.conclusion}", True

        sync_entry_fields(entry, decision)
        self._transition(entry, target, abnormal=False)
        return public_entry(entry), f"危险品已{action}，沿用首次结论：{decision.conclusion}", True

    def _find_submitted_duplicate(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        declaration_no = text(entry.get("申报编号"))
        for row in store.rows(MODULE):
            if row is entry or text(row.get("申报编号")) != declaration_no:
                continue
            if stored_decision(row) is not None or row.get("status") != STATUS_ORDER[0]:
                return row
        return None

    def _transition(self, entry: dict[str, Any], target: str, *, abnormal: bool) -> None:
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = abnormal
        if entry.get(MANAGED_MARK) == RULE_VERSION:
            entry["申报状态"] = target
