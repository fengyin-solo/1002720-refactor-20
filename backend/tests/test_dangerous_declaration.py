import unittest

from app.services.dangerous import DangerousService, MODULE, STATUS_ORDER
from app.services.dangerous_policy import evaluate_declaration
from app.store import store


class DangerousDeclarationPolicyTest(unittest.TestCase):
    def test_validates_class_and_un_number_with_one_segregation_decision(self):
        decision = evaluate_declaration({"危品类别": "三类", "联合国编号": "un1203"})

        self.assertTrue(decision.valid)
        self.assertEqual(decision.hazardous_class, "3")
        self.assertEqual(decision.un_number, "UN1203")
        self.assertEqual(decision.segregation_requirement, "易燃液体隔离")
        self.assertEqual(decision.packing_group, "未指定")
        self.assertEqual(decision.stowage_requirement, "常规积载")

    def test_invalid_class_and_un_number_produce_one_rejection_conclusion(self):
        decision = evaluate_declaration({"危品类别": "特殊危险品", "联合国编号": "BAD"})

        self.assertFalse(decision.valid)
        self.assertEqual(decision.conclusion, "申报驳回")
        self.assertEqual(decision.segregation_requirement, "待人工核定")
        self.assertEqual(len(decision.errors), 2)


class DangerousServiceFlowTest(unittest.TestCase):
    def setUp(self):
        store.rows(MODULE).clear()
        self.service = DangerousService()
        entry, missing = self.service.create_entry({
            "申报编号": "DANG-NEW-001",
            "箱号": "CSNU1000001",
            "危品类别": "3",
            "联合国编号": "1203",
        })
        self.assertFalse(missing)
        self.entry_id = int(entry["id"])

    def _entry(self):
        return store.find(MODULE, self.entry_id)

    def test_submit_approval_and_release_share_one_calculated_conclusion(self):
        entry, submit_message, submit_ok = self.service.run_action(self.entry_id, "提交申报")
        self.assertTrue(submit_ok)
        self.assertEqual(entry["危品类别"], "3")
        self.assertEqual(entry["联合国编号"], "UN1203")
        self.assertEqual(entry["隔离要求"], "易燃液体隔离")
        self.assertEqual(entry["申报状态"], STATUS_ORDER[1])

        first_snapshot = dict(self._entry()["_decision"])
        entry, approve_message, approve_ok = self.service.run_action(self.entry_id, "审核通过")
        self.assertTrue(approve_ok)
        self.assertEqual(entry["隔离要求"], "易燃液体隔离")
        self.assertEqual(entry["申报状态"], STATUS_ORDER[2])
        self.assertEqual(self._entry()["_decision"], first_snapshot)

        entry, release_message, release_ok = self.service.run_action(self.entry_id, "放行确认")
        self.assertTrue(release_ok)
        self.assertEqual(entry["隔离要求"], "易燃液体隔离")
        self.assertEqual(entry["申报状态"], STATUS_ORDER[3])
        self.assertEqual(self._entry()["_decision"], first_snapshot)
        self.assertNotIn("_decision", entry)
        self.assertNotIn("_managed_rule_version", entry)

    def test_repeated_submission_keeps_first_conclusion_even_when_fields_change(self):
        _, _, ok = self.service.run_action(self.entry_id, "提交申报")
        self.assertTrue(ok)

        entry = self._entry()
        entry["危品类别"] = "8"
        entry["联合国编号"] = "UN1789"
        repeated, message, repeated_ok = self.service.run_action(self.entry_id, "提交申报")

        self.assertTrue(repeated_ok)
        self.assertEqual(repeated["危品类别"], "3")
        self.assertEqual(repeated["联合国编号"], "UN1203")
        self.assertEqual(repeated["隔离要求"], "易燃液体隔离")
        self.assertIn("沿用首次结论", message)

    def test_same_declaration_number_in_another_record_only_accepts_first_submission(self):
        duplicate, missing = self.service.create_entry({
            "申报编号": "DANG-NEW-001",
            "箱号": "CSNU2000002",
            "危品类别": "8",
            "联合国编号": "UN1789",
        })
        self.assertFalse(missing)

        self.service.run_action(self.entry_id, "提交申报")
        entry, message, ok = self.service.run_action(int(duplicate["id"]), "提交申报")

        self.assertIsNone(entry)
        self.assertFalse(ok)
        self.assertIn("重复申报只认首次结论", message)

    def test_invalid_first_submission_blocks_later_actions(self):
        entry = self._entry()
        entry["联合国编号"] = "BAD"

        entry, message, ok = self.service.run_action(self.entry_id, "提交申报")
        self.assertFalse(ok)
        self.assertEqual(entry["status"], STATUS_ORDER[0])
        self.assertEqual(entry["隔离要求"], "待人工核定")
        self.assertIn("联合国编号必须为", message)

        entry, message, ok = self.service.run_action(self.entry_id, "审核通过")
        self.assertIsNone(entry)
        self.assertFalse(ok)
        self.assertEqual(message, "首次申报结论为申报驳回，不能继续审核或放行")

        self._entry()["联合国编号"] = "UN1789"
        repeated, message, repeated_ok = self.service.run_action(self.entry_id, "提交申报")
        self.assertFalse(repeated_ok)
        self.assertEqual(repeated["联合国编号"], "BAD")
        self.assertIn("重复申报只认首次结论", message)

    def test_seed_records_remain_on_their_original_values(self):
        store.rows(MODULE).append({
            "id": 999,
            "status": "待申报",
            "pending": True,
            "abnormal": False,
            "申报编号": "DANG-OLD-001",
            "箱号": "OLD-CONTAINER",
            "危品类别": "历史样例",
            "联合国编号": "OLD-UN",
            "包装等级": "历史包装",
            "积载要求": "历史积载",
            "隔离要求": "历史隔离",
            "申报状态": "历史状态",
        })

        for action in ("提交申报", "审核通过", "放行确认"):
            entry, _, ok = self.service.run_action(999, action)
            self.assertTrue(ok)
            self.assertEqual(entry["危品类别"], "历史样例")
            self.assertEqual(entry["联合国编号"], "OLD-UN")
            self.assertEqual(entry["隔离要求"], "历史隔离")
            self.assertEqual(entry["申报状态"], "历史状态")
            self.assertNotIn("_decision", entry)


if __name__ == "__main__":
    unittest.main()
