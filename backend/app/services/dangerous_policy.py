"""危险品申报的统一判定口径。

类别、联合国编号与隔离要求只在这里维护。申报链路中的提交、审核、放行都通过
``assess_dangerous_goods`` 得到同一份结论；有效申报在首次提交时固化结论，后续
动作只读取结果，避免不同环节重新计算造成分叉。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_CATEGORY = "1.4S"
DEFAULT_UN_NUMBER = "UN0000"
DEFAULT_PACKING_GROUP = "Ⅲ"
DEFAULT_STOWAGE_REQUIREMENT = "普通积载"
DEFAULT_SEGREGATION_REQUIREMENT = "无特殊隔离"
REJECTED_SEGREGATION_REQUIREMENT = "拒绝配载"

VALID_CATEGORIES = {
    "1",
    "1.1",
    "1.2",
    "1.3",
    "1.4",
    "1.4S",
    "1.5",
    "1.6",
    "2",
    "2.1",
    "2.2",
    "2.3",
    "3",
    "4.1",
    "4.2",
    "4.3",
    "5.1",
    "5.2",
    "6.1",
    "6.2",
    "7",
    "8",
    "9",
}

# 常见联合国编号与其所属类别。表中没有的编号只要编码范围合法，就按申报类别判定；
# 已知编号与申报类别不一致时直接拒绝，避免错误配载。
KNOWN_UN_CATEGORIES: dict[str, str] = {
    "UN0027": "1.1",
    "UN0044": "1.4S",
    "UN1001": "2.1",
    "UN1057": "2.1",
    "UN1072": "2.2",
    "UN1075": "2.1",
    "UN1203": "3",
    "UN1263": "3",
    "UN1500": "6.1",
    "UN1789": "8",
    "UN1950": "2.1",
    "UN1977": "2.2",
    "UN2067": "5.1",
    "UN2588": "6.1",
    "UN2794": "8",
    "UN2919": "7",
    "UN3077": "9",
    "UN3082": "9",
    "UN3268": "9",
    "UN3480": "9",
}

CATEGORY_SEGREGATION: dict[str, str] = {
    "1": "远离明火并保持爆炸品专舱隔离",
    "1.1": "爆炸品专舱隔离，远离明火与生活区",
    "1.2": "爆炸品专舱隔离，远离明火与生活区",
    "1.3": "爆炸品专舱隔离，远离明火",
    "1.4": "远离明火并保持爆炸品隔离",
    "1.4S": "远离明火并保持爆炸品隔离",
    "1.5": "爆炸品专舱隔离，远离明火与生活区",
    "1.6": "爆炸品专舱隔离，远离明火与生活区",
    "2": "与热源、火源分区积载",
    "2.1": "与火源、热源和氧化剂隔离",
    "2.2": "与腐蚀性物质保持通风隔离",
    "2.3": "与生活区、食品及热源隔离",
    "3": "与火源、热源和氧化剂隔离",
    "4.1": "与火源、热源和强酸隔离",
    "4.2": "远离热源，保持通风隔离",
    "4.3": "与水源、潮湿区域隔离",
    "5.1": "与易燃物、还原剂和腐蚀品隔离",
    "5.2": "控温积载，远离热源与易燃物",
    "6.1": "与生活区、食品和酸碱物质隔离",
    "6.2": "与食品、生活区和动物产品隔离",
    "7": "按辐射等级设置放射性专区分隔",
    "8": "与食品、生活区和金属表面隔离",
    "9": DEFAULT_SEGREGATION_REQUIREMENT,
}


@dataclass(frozen=True)
class DangerousAssessment:
    """危险品申报在一次链路中复用的判定结果。"""

    category: str
    un_number: str
    packing_group: str
    stowage_requirement: str
    segregation_requirement: str
    valid: bool
    errors: tuple[str, ...] = ()

    def to_public_fields(self) -> dict[str, str]:
        """返回对外字段名，字段集合保持旧接口不变。"""
        return {
            "危品类别": self.category,
            "联合国编号": self.un_number,
            "包装等级": self.packing_group,
            "积载要求": self.stowage_requirement,
            "隔离要求": self.segregation_requirement,
        }


@dataclass
class DangerousAssessmentPolicy:
    """用可替换的小规则表实现统一判定，便于测试和后续维护。"""

    def normalize_category(self, value: Any) -> str:
        text = self._text(value)
        if not text:
            return DEFAULT_CATEGORY
        text = text.replace("－", "-").replace("—", "-").upper().replace(" ", "")
        text = text.removeprefix("第").removesuffix("类")
        match = re.fullmatch(r"(\d)(?:\.(\d[GS]?))?", text)
        if not match:
            return text
        major = match.group(1)
        minor = match.group(2)
        return f"{major}.{minor}" if minor else major

    def normalize_un_number(self, value: Any) -> str:
        text = self._text(value).upper().replace(" ", "")
        if not text:
            return DEFAULT_UN_NUMBER
        match = re.fullmatch(r"(?:UN)?0*(\d{1,4})", text)
        if not match:
            return text if text.startswith("UN") else f"UN{text}"
        return f"UN{int(match.group(1)):04d}"

    def assess(self, values: dict[str, Any]) -> DangerousAssessment:
        category_raw = values.get("危品类别")
        un_raw = values.get("联合国编号")
        used_default_category = not self._text(category_raw)
        used_default_un_number = not self._text(un_raw)

        category = self.normalize_category(category_raw)
        un_number = self.normalize_un_number(un_raw)
        packing_group = self._text(values.get("包装等级")) or DEFAULT_PACKING_GROUP
        stowage_requirement = self._text(values.get("积载要求")) or DEFAULT_STOWAGE_REQUIREMENT

        errors: list[str] = []
        if category not in VALID_CATEGORIES:
            errors.append(f"危品类别「{self._text(category_raw) or category}」不在允许范围内")

        un_match = re.fullmatch(r"UN(\d{4})", un_number)
        un_value = int(un_match.group(1)) if un_match else -1
        if un_value < 0 or un_value > 3550:
            errors.append(f"联合国编号「{self._text(un_raw) or un_number}」不在 UN0000 至 UN3550 范围内")

        known_category = KNOWN_UN_CATEGORIES.get(un_number)
        if known_category and category != known_category:
            errors.append(f"联合国编号 {un_number} 应匹配危品类别 {known_category}，不能申报为 {category}")

        # 缺字段由统一默认值兜住，不把业务空缺继续传到后续环节。
        if used_default_category:
            category = DEFAULT_CATEGORY
        if used_default_un_number:
            un_number = DEFAULT_UN_NUMBER

        valid = not errors
        segregation = (
            CATEGORY_SEGREGATION.get(category)
            or CATEGORY_SEGREGATION.get(category.split(".", 1)[0])
            or DEFAULT_SEGREGATION_REQUIREMENT
        )
        if not valid:
            segregation = REJECTED_SEGREGATION_REQUIREMENT

        return DangerousAssessment(
            category=category,
            un_number=un_number,
            packing_group=packing_group,
            stowage_requirement=stowage_requirement,
            segregation_requirement=segregation,
            valid=valid,
            errors=tuple(errors),
        )

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


policy = DangerousAssessmentPolicy()


def assess_dangerous_goods(values: dict[str, Any]) -> DangerousAssessment:
    """按唯一口径计算危险品类别、编号、默认字段与隔离要求。"""
    return policy.assess(values)
