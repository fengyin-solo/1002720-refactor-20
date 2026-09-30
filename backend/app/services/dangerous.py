"""危险品申报业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.dangerous_policy import (
    DEFAULT_PACKING_GROUP,
    DEFAULT_SEGREGATION_REQUIREMENT,
    DEFAULT_STOWAGE_REQUIREMENT,
    DangerousAssessment,
    assess_dangerous_goods,
)

MODULE = "dangerous"
REQUIRED_FIELDS = ["申报编号", "箱号"]
OPTIONAL_FIELDS = ["危品类别", "联合国编号", "包装等级", "积载要求", "隔离要求"]
SUBMIT_FIELDS = ["危品类别", "联合国编号", "包装等级", "积载要求"]
STATUS_ORDER = ["待申报", "已申报", "海关审核", "已放行"]
ACTION_RULES = {"提交申报": "已申报", "审核通过": "海关审核", "放行确认": "已放行"}

FIELD_DEFAULTS = {
    "包装等级": DEFAULT_PACKING_GROUP,
    "积载要求": DEFAULT_STOWAGE_REQUIREMENT,
    "隔离要求": DEFAULT_SEGREGATION_REQUIREMENT,
}


class DangerousService:
    def __init__(self) -> None:
        # 结论不写进对外字段集合：对外仍只暴露原有业务字段。内存仓库重启后会重新
        # 播种，旧申报在服务启动时标记为 legacy，动作流转时继续沿用其原始结论。
        self._assessments: dict[int, dict[str, Any]] = {
            int(row["id"]): {"legacy": True, "submitted": False, "assessment": None}
            for row in store.rows(MODULE)
        }

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
        for row in rows:
            assessment = self._record(int(row["id"])).get("assessment")
            if assessment is not None:
                row.update(assessment.to_public_fields())
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        assessment = self._record(entry_id).get("assessment")
        if assessment is not None:
            entry.update(assessment.to_public_fields())
        return entry

    def create_entry(
        self,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str], bool]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, False

        declaration_no = str(values.get("申报编号") or "").strip()
        rows = store.rows(MODULE)
        existing = next(
            (row for row in rows if str(row.get("申报编号") or "").strip() == declaration_no),
            None,
        )
        if existing is not None:
            return existing, [], False

        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry.update({
            field: str(values.get(field) or "").strip() or FIELD_DEFAULTS[field]
            for field in OPTIONAL_FIELDS
            if field in FIELD_DEFAULTS
        })
        # 类别和编号留空时先放统一默认值；正式提交仍会走唯一校验口径。
        draft_assessment = assess_dangerous_goods(values)
        entry["危品类别"] = draft_assessment.category
        entry["联合国编号"] = draft_assessment.un_number
        entry["隔离要求"] = DEFAULT_SEGREGATION_REQUIREMENT
        entry["申报状态"] = STATUS_ORDER[0]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        self._assessments[entry["id"]] = {"legacy": False, "submitted": False, "assessment": None}
        return entry, [], True

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"危险品 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于危险品申报可执行范围"

        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        current_status = str(entry.get("status") or STATUS_ORDER[0])
        if current_status not in STATUS_ORDER:
            return None, f"当前状态「{current_status}」不在危险品申报状态序列里"
        current_index = self._status_index(current_status)
        target_index = self._status_index(target)

        if action == "提交申报":
            return self._submit_declaration(entry, current_index, target_index, values)
        return self._advance_declaration(
            entry, action, target, current_status, current_index, target_index
        )

    def _submit_declaration(
        self,
        entry: dict[str, Any],
        current_index: int,
        target_index: int,
        values: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, str]:
        record = self._record(int(entry["id"]))

        # 已经固化过结论的申报：重复提交只还原首次结论，不推进、不改写。
        if record.get("assessment") is not None:
            entry.update(record["assessment"].to_public_fields())
            return entry, "重复提交已忽略，沿用首次申报结论"

        if target_index != current_index + 1:
            return None, "当前申报已提交，请勿重复提交"

        # 旧申报不重算、不按新口径改写字段；合法第一次提交只推进状态，并把原始
        # 字段作为后续审核、放行复用的结论。
        if record.get("legacy"):
            assessment = self._build_legacy_assessment(entry)
            record["submitted"] = True
            record["assessment"] = assessment
            entry.update(assessment.to_public_fields())
            self._update_status(entry, STATUS_ORDER[target_index])
            return entry, "危险品已提交申报"

        for field in SUBMIT_FIELDS:
            text = str((values or {}).get(field) or "").strip()
            if text:
                entry[field] = text

        assessment = assess_dangerous_goods(entry)
        if not assessment.valid:
            return None, "；".join(assessment.errors)

        entry.update(assessment.to_public_fields())
        record["submitted"] = True
        record["assessment"] = assessment
        self._update_status(entry, STATUS_ORDER[target_index])
        return entry, "危险品已提交申报"

    def _advance_declaration(
        self,
        entry: dict[str, Any],
        action: str,
        target: str,
        current_status: str,
        current_index: int,
        target_index: int,
    ) -> tuple[dict[str, Any] | None, str]:
        if target_index != current_index + 1:
            return None, f"当前状态为{current_status}，不能执行「{action}」"

        record = self._record(int(entry["id"]))
        assessment = self._assessment_for_advance(entry, record)
        if assessment is None:
            return None, "请先完成提交申报，再执行后续环节"

        # 审核与放行只读取提交时固化的结论，不再重新计算类别、编号和隔离要求。
        entry.update({
            "危品类别": assessment.category,
            "联合国编号": assessment.un_number,
            "包装等级": assessment.packing_group,
            "积载要求": assessment.stowage_requirement,
            "隔离要求": assessment.segregation_requirement,
        })
        self._update_status(entry, target)
        return entry, f"危险品已{action}"

    def _assessment_for_advance(
        self,
        entry: dict[str, Any],
        record: dict[str, Any],
    ) -> DangerousAssessment | None:
        if record.get("assessment") is not None:
            return record["assessment"]
        if not record.get("legacy"):
            return None
        assessment = self._build_legacy_assessment(entry)
        record["submitted"] = True
        record["assessment"] = assessment
        return assessment

    def _build_legacy_assessment(self, entry: dict[str, Any]) -> DangerousAssessment:
        return DangerousAssessment(
            category=str(entry.get("危品类别") or ""),
            un_number=str(entry.get("联合国编号") or ""),
            packing_group=str(entry.get("包装等级") or ""),
            stowage_requirement=str(entry.get("积载要求") or ""),
            segregation_requirement=str(entry.get("隔离要求") or ""),
            valid=True,
        )

    def _record(self, entry_id: int) -> dict[str, Any]:
        return self._assessments.setdefault(
            entry_id,
            {"legacy": True, "submitted": False, "assessment": None},
        )

    @staticmethod
    def _status_index(status: str) -> int:
        try:
            return STATUS_ORDER.index(status)
        except ValueError:
            return 0

    @staticmethod
    def _update_status(entry: dict[str, Any], status: str) -> None:
        entry["status"] = status
        entry["申报状态"] = status
        entry["pending"] = status != STATUS_ORDER[-1]
        entry["abnormal"] = False
