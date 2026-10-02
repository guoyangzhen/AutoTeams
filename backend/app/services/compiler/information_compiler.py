"""Information Compiler —— 第一级编译器（PRD §4.3）。

输入：文件夹（复用 folder_scanner + document_processor）
处理：文件解析 + NER（命名实体识别）+ 结构化输出
输出：InformationCompileOutput（结构化信息条目列表）
置信度：基于文件解析成功率和实体提取覆盖率
"""
import logging
import os
import re
from typing import Optional

from app.services.compiler.base import (
    CompilerBase,
    CompilationContext,
    CompilationResult,
    Stage,
)
from app.services.cognition.knowledge_graph import EntityType
from app.schemas.compiler import InformationEntry, InformationCompileOutput
from app.services.llm_service import llm_service
from app.services import prompt_security
from app.services.compiler.base import safe_json_parse
from app.config import settings

logger = logging.getLogger(__name__)


# 文件类型 → 实体类型映射（用于初步分类）
FILE_TYPE_ENTITY_MAP = {
    "csv": [EntityType.EMPLOYEE, EntityType.PRODUCT, EntityType.CUSTOMER, EntityType.ORDER],
    "markdown": [EntityType.PROCESS, EntityType.KPI, EntityType.ROLE, EntityType.DEPARTMENT],
    "text": [EntityType.PROCESS, EntityType.ROLE, EntityType.KPI],
    "document": [EntityType.PROCESS, EntityType.ROLE, EntityType.DEPARTMENT],
}


class InformationCompiler(CompilerBase):
    """Information Compiler。

    负责将非结构化文件转换为结构化信息条目。
    流程：扫描文件夹 → 解析文件 → NER 抽取 → 结构化输出
    """

    stage = Stage.INFORMATION.value

    async def compile(self, ctx: CompilationContext) -> CompilationResult:
        """执行 Information 级编译。"""
        folder_path = ctx.folder_path or ""
        self.logger.info(f"Information 编译开始: enterprise={ctx.enterprise_id}, folder={folder_path}")

        # 确定 allowed_root：示例数据目录下的路径使用 SAMPLE_DATA_DIR，否则用默认（UPLOAD_ROOT）
        allowed_root = self._get_allowed_root(folder_path)

        entries: list[InformationEntry] = []
        total_files = 0
        parsed_files = 0

        # 扫描文件夹（复用 folder_scanner）
        scanned_files = self._scan_folder(folder_path, allowed_root)
        total_files = len(scanned_files)

        for sf in scanned_files:
            try:
                # 解析文件内容（复用 document_processor）
                content = await self._parse_file(sf.path, allowed_root)
                if not content:
                    continue
                parsed_files += 1

                # NER 抽取
                file_entries = await self._extract_entities(ctx, content, sf)
                entries.extend(file_entries)
            except Exception as e:
                self.logger.warning(f"文件解析失败 {sf.path}: {e}")

        # 计算置信度
        confidence = self.calculate_confidence(total_files, parsed_files)

        self.logger.info(
            f"Information 编译完成: enterprise={ctx.enterprise_id}, "
            f"files={total_files}, parsed={parsed_files}, entries={len(entries)}"
        )

        output = InformationCompileOutput(
            enterprise_id=ctx.enterprise_id,
            entries=entries,
            total_files=total_files,
            confidence=confidence,
        )

        summary = self._generate_summary(entries, total_files, parsed_files)

        return CompilationResult(
            stage=self.stage,
            output=output,
            confidence=confidence,
            discovered_summary=summary,
            discovered_count=len(entries),
        )

    def _get_allowed_root(self, folder_path: str) -> Optional[str]:
        """确定路径校验的 allowed_root。

        示例数据目录（SAMPLE_DATA_DIR）下的路径使用 SAMPLE_DATA_DIR 作为 allowed_root，
        使 scan_folder / process_document 的路径校验能通过（这些路径由服务端控制，安全可信）。
        其他路径返回 None，默认使用 UPLOAD_ROOT。
        """
        if not folder_path:
            return None
        try:
            sample_root = os.path.realpath(settings.SAMPLE_DATA_DIR)
            real_folder = os.path.realpath(folder_path)
            if real_folder == sample_root or real_folder.startswith(sample_root + os.sep):
                return sample_root
        except Exception as exc:
            # 路径探测失败按"未命中样例数据"处理，不阻断编译。
            logger.debug("样例数据路径探测失败: %s", exc)
        return None

    def _scan_folder(self, folder_path: str, allowed_root: Optional[str] = None) -> list:
        """扫描文件夹（复用 folder_scanner.scan_folder）。"""
        if not folder_path or not os.path.isdir(folder_path):
            return []
        try:
            from app.services.folder_scanner import scan_folder
            return scan_folder(folder_path, recursive=True, allowed_root=allowed_root)
        except Exception as e:
            self.logger.warning(f"文件夹扫描失败: {e}")
            return []

    async def _parse_file(self, file_path: str, allowed_root: Optional[str] = None) -> str:
        """解析文件内容（复用 document_processor.process_document）。"""
        try:
            from app.services.document_processor import process_document
            return await process_document(file_path, allowed_root=allowed_root)
        except Exception as e:
            self.logger.warning(f"文件解析失败 {file_path}: {e}")
            return ""

    async def _extract_entities(self, ctx: CompilationContext, content: str, scanned_file) -> list[InformationEntry]:
        """从文件内容中抽取实体（NER）。

        使用 LLM 进行命名实体识别，识别部门/角色/产品/客户/流程等实体。
        用户输入内容经 prompt_security.wrap_untrusted 包裹。
        """
        if not content or len(content.strip()) < 10:
            return []

        # 基于文件类型确定候选实体类型
        file_type = self._classify_file(scanned_file)
        candidate_types = FILE_TYPE_ENTITY_MAP.get(file_type, [EntityType.PROCESS])

        entries: list[InformationEntry] = []

        # 使用 LLM 进行 NER
        try:
            entities = await self._llm_ner(ctx, content, candidate_types, scanned_file.path, file_type)
            entries.extend(entities)
        except Exception as e:
            self.logger.warning(f"LLM NER 失败: {e}")
            # 降级：基于文件名生成基础条目
            entries.append(InformationEntry(
                entry_id=self._make_entry_id("info"),
                entry_type=candidate_types[0].value,
                name=scanned_file.name if hasattr(scanned_file, "name") else os.path.basename(scanned_file.path),
                attributes={"source": "filename_fallback"},
                source_file=scanned_file.path,
                file_type=file_type,
                confidence=0.3,
            ))

        # 规则兜底：从组织架构文档确定性提取部门/岗位
        # （LLM 演示模式/未配置 API 时 NER 常遗漏 Department/Role，导致运行时
        #   组织架构被清空。此处用确定性规则解析 org-structure 类文档，保证组织数据不丢失。）
        entries.extend(self._rule_based_org_extraction(content, scanned_file.path, file_type))

        return entries

    _DEPT_NAME_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,20}(?:部|中心|委员会|组|处|科|室|BU|事业部))")
    # 组织架构树行：`├── 销售部 — 总监…` / `└── 生产部 — 经理…` / 缩进 + 分支符
    _TREE_LINE_RE = re.compile(
        r"(?P<indent>[\s│]+)?(?P<branch>[├└]──|\|)\s*"
        r"(?P<name>[\u4e00-\u9fa5A-Za-z0-9（）()·\-]{2,30})"
    )

    def _rule_based_org_extraction(
        self,
        content: str,
        source_file: str,
        file_type: str,
    ) -> list[InformationEntry]:
        """从组织架构 markdown 文档确定性提取 Department / Role 实体。

        兼容两种常见书写格式：
        1. 组织架构树（`├──/└──` 分支 + 缩进表层级）
        2. markdown 表格（`| 部门 | 负责人 | 职责 |`）

        产出 Department 条目（含 parent_dept_id / level）与 Role 条目（含 department），
        使运行时编译器在演示模式（无 LLM API）下也能正确构建组织架构与岗位能力矩阵。
        """
        if file_type not in ("markdown", "document", "text"):
            return []

        # 只对真正的「组织架构」文档做确定性子部门/岗位提取。
        # 此前对每个 markdown 都跑规则，会把销售 SOP、客服 SOP、HR 手册等文档里的
        # 「客服部/部门协调/亲自处理」等文字误当成部门，污染运行时组织数据。
        # 这里按文件名强信号（组织/org/structure/架构/人事/部门架构）过滤，其余文档交由 LLM NER。
        base = os.path.basename(source_file).lower()
        if not any(marker in base for marker in (
            "组织", "org", "structure", "架构", "部门", "人事", "staff", "employee"
        )):
            return []

        entries: list[InformationEntry] = []
        company_name = ""
        # 树解析：dept 行与 role 行，附带层级缩进深度
        tree_rows: list[dict] = []
        # 表格解析
        table_rows: list[dict] = []

        lines = content.splitlines()
        in_code_block = False
        table_header_seen = False

        for raw in lines:
            line = raw.rstrip()
            stripped = line.strip()

            # 跳过代码块分隔符（org 树常包在 ``` 内）
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                if not stripped:
                    continue
                # 组织架构树行
                m = self._TREE_LINE_RE.match(line)
                if m:
                    name = m.group("name")
                    indent = m.group("indent") or ""
                    depth = indent.count("│") + indent.count("│")  # 一个 │ 一个层级
                    # 用缩进空格数辅助判断层级（非 │ 前缀的场景）
                    indent_spaces = len(indent.expandtabs(4))
                    level = max(1, indent_spaces // 4 + 1)
                    tree_rows.append({"name": name, "level": level, "depth": depth})
                continue

            # 表格行
            if stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if table_header_seen and cells and cells[0]:
                    table_rows.append({"name": cells[0], "cell_start": cells[0]})
                # 表头：含「部门」或「岗位」字样
                if any("部门" in c or "岗位" in c or "职位" in c for c in cells):
                    table_header_seen = True
                continue
            table_header_seen = False

        # —— 公司根节点 ——
        # 兼容 `# XXX有限公司 — 组织架构` / `# XXX公司组织架构` 等写法
        for line in lines[:20]:
            if line.startswith("# ") and line.strip() != "#":
                raw_company = line.strip().lstrip("# ").strip()
                # 去掉「— 组织架构」「组织架构」等后缀，保留公司名
                company_name = re.split(r"[—\-–]\s*组织架构|组织架构", raw_company)[0].strip()
                break
        if not company_name:
            # 从首行组织架构树根节点取名
            company_name = "企业"

        # —— 解析出部门集合（去重、保序）——
        dept_names: list[str] = []
        seen_dept: set[str] = set()
        def _add_dept(name: str) -> None:
            if name and name not in seen_dept:
                seen_dept.add(name)
                dept_names.append(name)

        # 从树中提取：level==2 的父级通常为部门（文件通常把根=1, 部门=2, 岗位=3）
        # 但格式不一，采用「部门名特征」直接识别树中带「部/中心/事业部」等后缀的节点
        role_names: list[str] = []
        for row in tree_rows:
            name = row["name"]
            if self._DEPT_NAME_RE.search(name):
                _add_dept(name)
            else:
                role_names.append(name)

        # 从表格提取部门
        for row in table_rows:
            name = row["name"]
            if self._DEPT_NAME_RE.search(name):
                _add_dept(name)

        # —— 生成 Department 条目 ——
        if dept_names:
            company_id = f"dept_{self._make_entry_id('co')}"
            entries.append(InformationEntry(
                entry_id=company_id,
                entry_type=EntityType.DEPARTMENT.value,
                name=company_name,
                attributes={"level": 0, "parent_id": "", "source": "rule_based"},
                source_file=source_file,
                file_type=file_type,
                confidence=0.9,
            ))
            for dept in dept_names:
                entries.append(InformationEntry(
                    entry_id=self._make_entry_id("dept"),
                    entry_type=EntityType.DEPARTMENT.value,
                    name=dept,
                    attributes={
                        "level": 1,
                        "parent_id": company_id,
                        "source": "rule_based",
                    },
                    source_file=source_file,
                    file_type=file_type,
                    confidence=0.85,
                ))

        # —— 生成 Role 条目（岗位，含所属部门）——
        # 树中未被识别为部门的节点视为岗位；若存在部门名，也把「岗位名」与部门关联
        for role in role_names:
            dept_attr = ""
            for d in dept_names:
                if d in role or role in d:
                    dept_attr = d
                    break
            entries.append(InformationEntry(
                entry_id=self._make_entry_id("role"),
                entry_type=EntityType.ROLE.value,
                name=role,
                attributes={
                    "department": dept_attr,
                    "level": "L2",
                    "source": "rule_based",
                },
                source_file=source_file,
                file_type=file_type,
                confidence=0.7,
            ))

        return entries

    def _classify_file(self, scanned_file) -> str:
        """分类文件类型。"""
        path = scanned_file.path if hasattr(scanned_file, "path") else str(scanned_file)
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext == "csv":
            return "csv"
        if ext in ("md", "markdown"):
            return "markdown"
        if ext in ("txt",):
            return "text"
        if ext in ("pdf", "docx", "doc", "xlsx", "pptx"):
            return "document"
        return "text"

    async def _llm_ner(
        self,
        ctx: CompilationContext,
        content: str,
        candidate_types: list,
        source_file: str,
        file_type: str,
    ) -> list[InformationEntry]:
        """使用 LLM 进行命名实体识别。

        用户输入内容经 prompt_security.wrap_untrusted 包裹。
        """
        # 截断过长内容（提高到 8000 以覆盖组织架构等长文档的关键段落）
        truncated = content[:8000]

        type_names = [t.value for t in candidate_types]
        # 实体类型定义与示例（引导 LLM 正确区分 Role 与 Department）
        type_definitions = {
            "Department": "部门——组织架构中的职能单元，如「销售部」「财务部」「研发部」",
            "Role": "岗位/职位——具体的工作角色（非部门），如「销售经理」「客服专员」「CEO」「会计」「硬件工程师」。组织架构树中每个人对应的职位名称都是 Role",
            "Process": "流程——业务操作流程/SOP，如「销售SOP」「报价审批流程」「售后服务流程」",
            "KPI": "指标——绩效考核指标/指标体系，如「岗位KPI矩阵」「客户满意度指标」",
            "Employee": "员工——具体的人员姓名（含工号），如「张明远」「李婉清」",
            "Product": "产品——产品名称/型号，如「SL-CT800 温控器」",
            "Customer": "客户——客户公司/客户名称",
            "Order": "订单——订单编号/订单记录",
            "Permission": "权限——数据/操作权限项，如「查看全部客户」「审批5-20万报价」",
            "System": "业务系统——使用的软件/系统，如「CRM系统」「ERP系统」",
            "Knowledge": "知识条目——文档/知识库中的知识单元",
            "Opportunity": "商机——销售商机/商机记录",
            "Tool": "工具——使用的工具/平台",
        }
        definitions_text = "；".join(
            f"{t}：{type_definitions.get(t, t)}"
            for t in type_names
        )
        system_prompt = (
            "你是企业信息抽取专家。从给定的文档内容中识别企业实体。\n"
            f"需要识别的实体类型及定义：\n{definitions_text}\n\n"
            "关键规则：\n"
            "1. Department 是部门名称（如「销售部」），Role 是具体岗位/职位（如「销售经理」「销售代表」）。\n"
            "   组织架构树中每个人对应的职位（如 CEO、总监、经理、专员、工程师）都是 Role，必须逐一提取。\n"
            "2. 同一文档中可能同时存在 Department 和 Role，请分别提取。\n"
            "3. 尽可能从文档中提取 attributes 中的 description（描述）、responsibilities（职责）、"
            "department（所属部门）、level（层级 L1-L4）等字段。\n\n"
            "请以 JSON 数组格式输出，每个实体包含：name（名称）、entity_type（类型，必须是上述类型之一）、"
            "attributes（属性字典）。仅输出 JSON，不要其他文字。"
        )

        # 安全包裹用户输入
        user_message = prompt_security.wrap_untrusted(truncated)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        response = await llm_service.chat(
            messages=messages,
            temperature=0.1,
            max_tokens=4096,
        )

        # 安全解析 JSON
        entities_data = safe_json_parse(response)
        # 兼容 LLM 返回 dict 包裹的情况（如 {"entities": [...]} 或 {"data": [...]}）
        if isinstance(entities_data, dict):
            for key in ("entities", "data", "results", "items"):
                if key in entities_data and isinstance(entities_data[key], list):
                    entities_data = entities_data[key]
                    break
        if not isinstance(entities_data, list):
            self.logger.debug(
                f"NER 返回非列表格式 (type={type(entities_data).__name__}), "
                f"source={source_file}, response_preview={str(response)[:200]}"
            )
            return []

        entries: list[InformationEntry] = []
        for item in entities_data:
            if not isinstance(item, dict) or "name" not in item:
                continue
            entity_type = item.get("entity_type", candidate_types[0].value)
            # 验证实体类型合法性
            valid_types = {e.value for e in EntityType}
            if entity_type not in valid_types:
                entity_type = candidate_types[0].value

            entries.append(InformationEntry(
                entry_id=self._make_entry_id("info"),
                entry_type=entity_type,
                name=str(item["name"])[:200],
                attributes={
                    k: v for k, v in item.get("attributes", {}).items()
                    if isinstance(v, (str, int, float, bool, list, dict))
                },
                source_file=source_file,
                file_type=file_type,
                confidence=0.75,
            ))

        return entries

    def _generate_summary(self, entries: list[InformationEntry], total: int, parsed: int) -> str:
        """生成发现摘要（供编译动画展示）。"""
        from app.services.compiler.i18n import entity_label
        type_counts: dict[str, int] = {}
        for e in entries:
            type_counts[e.entry_type] = type_counts.get(e.entry_type, 0) + 1
        parts = [f"扫描 {total} 个文件，解析 {parsed} 个"]
        if type_counts:
            type_str = "、".join(
                f"{entity_label(t)} {c} 个"
                for t, c in sorted(type_counts.items(), key=lambda x: -x[1])
            )
            parts.append(f"发现 {type_str}")
        return "；".join(parts)
