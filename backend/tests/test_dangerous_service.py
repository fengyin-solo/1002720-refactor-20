"""危险品申报统一口径与三步状态流转的回归测试。"""
from __future__ import annotations

import unittest

from app.services.dangerous import DangerousService
from app.services.dangerous_policy import assess_dangerous_goods
from app.store import store


def fresh_service() -> DangerousService:
    store.reset()
    return DangerousService()


class DangerousPolicyTest(unittest.TestCase):
    def test_normalizes_category_and_un_number(self) -> None:
        assessment = assess_dangerous_goods({"危品类别": "第3类", "联合国编号": "1203"})
        self.assertTrue(assessment.valid, assessment.errors)
        self.assertEqual(assessment.category, "3")
        self.assertEqual(assessment.un_number, "UN1203")
        self.assertEqual(assessment.segregation_requirement, "与火源、热源和氧化剂隔离")

    def test_defaults_cover_blank_fields(self) -> None:
        assessment = assess_dangerous_goods({})
        self.assertTrue(assessment.valid, assessment.errors)
        self.assertEqual(assessment.category, "1.4S")
        self.assertEqual(assessment.un_number, "UN0000")
        self.assertEqual(assessment.packing_group, "Ⅲ")
        self.assertEqual(assessment.stowage_requirement, "普通积载")

    def test_rejects_mismatched_known_un_number(self) -> None:
        assessment = assess_dangerous_goods({"危品类别": "3", "联合国编号": "UN1001"})
        self.assertFalse(assessment.valid)
        self.assertIn("联合国编号 UN1001 应匹配危品类别 2.1", "；".join(assessment.errors))


class DangerousWorkflowTest(unittest.TestCase):
    def test_first_submission_locks_conclusion_for_later_actions(self) -> None:
        service = fresh_service()
        entry, missing, _ = service.create_entry({
            "申报编号": "DANG-TEST-1",
            "箱号": "CN-TEST-1",
            "危品类别": "3",
            "联合国编号": "UN1203",
        })
        self.assertFalse(missing)
        self.assertEqual(entry["隔离要求"], "无特殊隔离")

        submitted, message = service.run_action(entry["id"], "提交申报")
        self.assertIsNotNone(submitted)
        self.assertEqual(message, "危险品已提交申报")
        assert submitted is not None
        self.assertEqual(submitted["隔离要求"], "与火源、热源和氧化剂隔离")
        first_conclusion = submitted.copy()

        submitted["危品类别"] = "2.1"
        repeated, repeat_message = service.run_action(entry["id"], "提交申报")
        assert repeated is not None
        self.assertEqual(repeat_message, "重复提交已忽略，沿用首次申报结论")
        self.assertEqual(repeated["危品类别"], "3")

        audited, _ = service.run_action(entry["id"], "审核通过")
        assert audited is not None
        self.assertEqual(audited["status"], "海关审核")
        audited_conclusion = audited.copy()
        released, _ = service.run_action(entry["id"], "放行确认")
        assert released is not None
        self.assertEqual(released["status"], "已放行")
        for field in ("危品类别", "联合国编号", "包装等级", "积载要求", "隔离要求"):
            self.assertEqual(audited_conclusion[field], first_conclusion[field])
            self.assertEqual(released[field], first_conclusion[field])

    def test_duplicate_create_returns_first_declaration(self) -> None:
        service = fresh_service()
        first, missing, _ = service.create_entry({
            "申报编号": "DANG-DUP",
            "箱号": "CN-FIRST",
            "危品类别": "3",
            "联合国编号": "UN1203",
        })
        second, second_missing, second_created = service.create_entry({
            "申报编号": "DANG-DUP",
            "箱号": "CN-SECOND",
            "危品类别": "8",
            "联合国编号": "UN1789",
        })
        self.assertFalse(missing)
        self.assertFalse(second_missing)
        self.assertFalse(second_created)
        self.assertIs(first, second)
        self.assertEqual(second["箱号"], "CN-FIRST")
        self.assertEqual(second["危品类别"], "3")

    def test_first_submission_accepts_fields_but_locks_them_afterward(self) -> None:
        service = fresh_service()
        entry, _, _ = service.create_entry({
            "申报编号": "DANG-TEST-SUBMIT",
            "箱号": "CN-TEST-SUBMIT",
        })
        submitted, message = service.run_action(entry["id"], "提交申报", {
            "action": "提交申报",
            "危品类别": "2.1",
            "联合国编号": "1001",
        })
        self.assertIsNotNone(submitted)
        self.assertEqual(message, "危险品已提交申报")
        assert submitted is not None
        self.assertEqual(submitted["危品类别"], "2.1")
        self.assertEqual(submitted["联合国编号"], "UN1001")
        self.assertEqual(submitted["隔离要求"], "与火源、热源和氧化剂隔离")

        repeated, _ = service.run_action(entry["id"], "提交申报", {
            "action": "提交申报",
            "危品类别": "3",
            "联合国编号": "UN1203",
        })
        assert repeated is not None
        self.assertEqual(repeated["危品类别"], "2.1")
        self.assertEqual(repeated["联合国编号"], "UN1001")

    def test_invalid_submission_keeps_declaration_pending_without_conclusion(self) -> None:
        service = fresh_service()
        entry, _, _ = service.create_entry({
            "申报编号": "DANG-TEST-2",
            "箱号": "CN-TEST-2",
            "危品类别": "3",
            "联合国编号": "UN1001",
        })
        result, message = service.run_action(entry["id"], "提交申报")
        self.assertIsNone(result)
        self.assertIn("应匹配危品类别 2.1", message)
        stored = service.get_entry(entry["id"])
        assert stored is not None
        self.assertEqual(stored["status"], "待申报")

    def test_action_must_follow_chain_order(self) -> None:
        service = fresh_service()
        entry, _, _ = service.create_entry({
            "申报编号": "DANG-TEST-3",
            "箱号": "CN-TEST-3",
            "危品类别": "3",
            "联合国编号": "UN1203",
        })
        result, message = service.run_action(entry["id"], "审核通过")
        self.assertIsNone(result)
        self.assertEqual(message, "当前状态为待申报，不能执行「审核通过」")

    def test_legacy_pending_declaration_submits_once_without_recalculating(self) -> None:
        service = fresh_service()
        original = service.get_entry(1).copy()
        submitted, message = service.run_action(1, "提交申报", {
            "action": "提交申报",
            "危品类别": "3",
            "联合国编号": "UN1203",
        })
        assert submitted is not None
        self.assertEqual(message, "危险品已提交申报")
        self.assertEqual(submitted["status"], "已申报")
        for field in ("危品类别", "联合国编号", "包装等级", "积载要求", "隔离要求"):
            self.assertEqual(submitted[field], original[field])

        repeated, repeat_message = service.run_action(1, "提交申报")
        assert repeated is not None
        self.assertEqual(repeat_message, "重复提交已忽略，沿用首次申报结论")
        self.assertEqual(repeated["status"], "已申报")
        for field in ("危品类别", "联合国编号", "包装等级", "积载要求", "隔离要求"):
            self.assertEqual(repeated[field], original[field])

    def test_legacy_declaration_keeps_original_conclusion(self) -> None:
        service = fresh_service()
        original = service.get_entry(2).copy()
        audited, audit_message = service.run_action(2, "审核通过")
        assert audited is not None
        self.assertEqual(audit_message, "危险品已审核通过")
        released, release_message = service.run_action(2, "放行确认")
        assert released is not None
        self.assertEqual(release_message, "危险品已放行确认")
        for field in ("危品类别", "联合国编号", "包装等级", "积载要求", "隔离要求"):
            self.assertEqual(audited[field], original[field])
            self.assertEqual(released[field], original[field])


if __name__ == "__main__":
    unittest.main()
