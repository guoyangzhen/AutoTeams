"""行业模板资产加载器（AutoTeams 5.0）。

把 ``assets/*.json`` 在**加载期**用 Pydantic 校验后转成 4.0 兼容的 dict 列表，
供 ``template_service`` 使用。这样：

- 巨型文件里内联的模板字面量被彻底搬出（``template_service.py`` 从 1194 行
  降到只剩 DB 编排逻辑），符合重构计划 §4.2.2「单文件 ≤ 400 行」；
- 资产写错字段名、漏必填项、``skill_ids`` 指向不存在的 code，都会在**首次加载**
  时抛 ``IndustryAsset`` 的校验错误，而不是等某个企业套用模板时才暴露。

资产清单：
- ``legacy_roles.json``  4.0 预置的 10 个岗位模板（客服/销售/售前/售后/HR/运营/
                      财务/医疗/教育/法务）+ 40 个技能，原样保留以维持既有测试
                      对 ``PRESET_*`` 的断言。
- ``<industry>.json``    重构计划 §决策一的 6 大中小企业行业模板
                      （智能制造/电商零售/跨境出海/专业咨询/教育培训/医疗健康）。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from pydantic import ValidationError

from app.services.templates.schema import INDUSTRIES, IndustryAsset, SkillAsset

logger = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).parent / "assets"
_LEGACY_ASSET = "legacy_roles.json"


class TemplateAssetError(RuntimeError):
    """模板资产缺失或不符合 schema。"""


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise TemplateAssetError(f"模板资产缺失: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateAssetError(f"模板资产 {path.name} 不是合法 JSON: {exc}") from exc


@lru_cache(maxsize=1)
def _load_legacy() -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """加载 4.0 预置模板（40 skill + 10 agent），原样返回。"""
    raw = _read_json(_ASSET_DIR / _LEGACY_ASSET)
    if not isinstance(raw, dict) or "skills" not in raw or "agents" not in raw:
        raise TemplateAssetError(f"{_LEGACY_ASSET} 需包含 skills 与 agents 两个键")
    skills, agents = raw["skills"], raw["agents"]
    if not isinstance(skills, list) or not isinstance(agents, list):
        raise TemplateAssetError(f"{_LEGACY_ASSET} 的 skills/agents 必须是数组")
    return skills, agents


@lru_cache(maxsize=1)
def load_industry_assets() -> List[IndustryAsset]:
    """加载并校验 6 大行业资产。

    任何一份 JSON 不合 schema 都在此处失败——**不静默跳过**，
    否则企业选了某个行业却悄悄拿不到模板，问题会拖到运行时才暴露。
    """
    assets: List[IndustryAsset] = []
    seen: Dict[str, Path] = {}
    for industry in INDUSTRIES:
        path = _ASSET_DIR / f"{industry}.json"
        payload = _read_json(path)
        try:
            asset = IndustryAsset.model_validate(payload)
        except ValidationError as exc:
            raise TemplateAssetError(f"行业模板 {path.name} 校验失败:\n{exc}") from exc
        if asset.industry != industry:
            raise TemplateAssetError(
                f"行业模板 {path.name} 的 industry 字段为 {asset.industry!r}，"
                f"与文件名声明的 {industry!r} 不一致"
            )
        if asset.industry in seen:
            raise TemplateAssetError(f"行业 {asset.industry} 存在重复资产文件")
        seen[asset.industry] = path
        assets.append(asset)
    logger.info("已加载 %d 个行业模板资产: %s", len(assets), list(seen))
    return assets


def list_industries() -> List[Dict[str, str]]:
    """6 大行业清单（标识 + 中文名 + 简介），供设置页/引导页展示。"""
    return [
        {
            "industry": a.industry,
            "industry_name": a.industry_name,
            "description": a.description,
            "agent_count": len(a.agents),
            "skill_count": len(a.skills),
        }
        for a in load_industry_assets()
    ]


def get_industry_asset(industry: str) -> IndustryAsset:
    """按标识取行业资产；不存在则抛错（不返回空对象）。"""
    for asset in load_industry_assets():
        if asset.industry == industry:
            return asset
    raise TemplateAssetError(f"未知行业模板: {industry}")


def preset_skill_templates() -> List[Dict[str, Any]]:
    """4.0 兼容视图：预置 SkillTemplate 列表（40 条）。"""
    return _load_legacy()[0]


def preset_agent_templates() -> List[Dict[str, Any]]:
    """4.0 兼容视图：预置 AgentTemplate 列表（10 条）。"""
    return _load_legacy()[1]


def industry_skill_templates() -> List[Dict[str, Any]]:
    """6 大行业的全部技能（跨行业合并，code 全局唯一）。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for asset in load_industry_assets():
        for skill in asset.skills:
            if skill.code in seen:
                raise TemplateAssetError(f"行业间 skill code 冲突: {skill.code}")
            seen.add(skill.code)
            out.append(SkillAsset.model_validate(skill.model_dump()).model_dump())
    return out


def industry_agent_templates() -> List[Dict[str, Any]]:
    """6 大行业的全部数字员工模板（跨行业合并）。"""
    return [d for asset in load_industry_assets() for d in asset.all_agent_dicts()]


def reset_cache() -> None:
    """清空加载缓存（供测试在 monkeypatch 资产后强制重载）。"""
    _load_legacy.cache_clear()
    load_industry_assets.cache_clear()
