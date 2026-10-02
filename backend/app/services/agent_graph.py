"""Agent 构建编排引擎（基于 LangGraph 1.0 状态机 + HITL + 状态持久化）。

重构说明（P1-2）：
之前 agent_builder.build_agent 是一个单一函数，整个流程在一个 try-except 块中
顺序执行，无法暂停/恢复/重试，无法可视化执行进度，无法在关键步骤人工审批。

现升级为 LangGraph 状态机：
1. 状态图：planner → scanner → [HITL approval] → parser → vectorizer → builder → tester
2. HITL 检查点：扫描后暂停等待人工审批（可配置是否启用）
3. 状态持久化：SqliteSaver 保存执行状态，重启后可恢复
4. 进度可视化：每个节点执行时更新 state.current_step 和 messages
5. 错误恢复：单节点失败可重试，无需重新执行整个流程

与现有 build_agent 的关系：
- build_agent 保留为兼容入口（旧调用方式不变）
- build_agent_via_graph 为新入口（支持 HITL + 持久化）
- 两者共享底层服务（folder_scanner、document_processor、chunker_service、vector_store）

LangGraph 状态图：
    START
      ↓
    planner  ─── 创建 Agent 记录，状态设为 processing
      ↓
    scanner  ─── 扫描文件夹，识别文件类型
      ↓
    approval ─── [HITL] 暂停等待人工审批扫描结果（可选）
      ↓
    parser   ─── 解析每个文件 + 结构感知分块
      ↓
    vectorizer ─ 向量化存入 ChromaDB
      ↓
    builder  ─── 生成 system_prompt + 默认 skills
      ↓
    tester   ─── 测试 Agent 是否可用
      ↓
    END
"""
import asyncio
import logging
import uuid
from operator import add
from typing import Annotated, Optional, TypedDict

import chromadb.errors
import httpx
from sqlalchemy import select

from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.file import File
from app.services.folder_scanner import scan_folder
from app.services.rag.chunker_service import chunker_service
from app.services.vector_store import VectorStoreService
from app.utils.metrics import errors_total
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

# BE-PER-01: LangGraph parser 节点文件解析最大并发数
_MAX_CONCURRENT_FILE_PROCESSING = 3


# ============================================================
# 1. 状态定义
# ============================================================

class AgentBuildState(TypedDict, total=False):
    """Agent 构建流程的状态。

    LangGraph 节点函数接收完整 state，返回 dict（要更新的字段）。
    total=False 表示所有字段都是可选的（首次调用时只有部分字段）。
    """
    # 输入参数
    enterprise_id: str
    name: str
    description: str
    folder_path: str

    # P1-6.4: Setup 向导差异化参数（模型 / 索引策略 / 已选技能）
    model: Optional[str]
    skills: Optional[list[str]]
    index_strategy: Optional[str]

    # 各阶段输出
    agent_id: str                 # planner 创建的 Agent ID
    files: list                   # scanner 扫描到的文件列表
    file_types: dict              # scanner 统计的文件类型
    chunks_by_file: list          # parser 输出：[{file_name, file_path, file_type, chunks}]
    total_chunks: int             # parser 输出：总块数
    vector_count: int             # vectorizer 输出：向量数
    system_prompt: str            # builder 输出：生成的系统提示
    skills_created: int           # builder 输出：创建的 skill 数
    test_result: dict             # tester 输出：测试结果

    # 流程控制
    current_step: str             # 当前执行的节点名
    status: str                   # running / paused / completed / failed
    error: Optional[str]           # 错误信息
    require_approval: bool        # 是否启用 HITL 审批

    # 日志累加（每次 append）：使用 operator.add 作为 reducer
    # 注意：LangGraph 期望的是可调用的 reducer 函数，不是字符串 "add"
    messages: Annotated[list[str], add]


# ============================================================
# 2. 节点函数
# ============================================================

def make_planner_node(db_session_factory):
    """创建 planner 节点：在 DB 中创建 Agent 记录。

    需要传入 db_session_factory（async_session_factory）来创建独立 session。
    """
    async def planner_node(state: AgentBuildState) -> dict:
        logger.info(f"[LangGraph:planner] 开始构建 Agent: {state['name']}")
        agent_id = str(uuid.uuid4())

        async with db_session_factory() as db:
            agent = Agent(
                id=agent_id,
                enterprise_id=state["enterprise_id"],
                name=state["name"],
                description=state.get("description", ""),
                folder_path=state.get("folder_path"),
                system_prompt="",
                status="processing",
                file_count=0,
                knowledge_count=0,
            )
            db.add(agent)
            await db.commit()

        return {
            "agent_id": agent_id,
            "current_step": "planner",
            "status": "running",
            "messages": [f"[planner] Agent 记录已创建，ID={agent_id}"],
        }

    return planner_node


async def scanner_node(state: AgentBuildState) -> dict:
    """P0-PERF: scanner 节点异步化。

    scan_folder 使用 os.walk 同步遍历大文件夹，会阻塞事件循环；
    通过 asyncio.to_thread 放到线程池执行。
    """
    logger.info(f"[LangGraph:scanner] 扫描文件夹: {state['folder_path']}")
    files = await asyncio.to_thread(scan_folder, state["folder_path"])

    # 统计文件类型
    file_types: dict[str, int] = {}
    for f in files:
        file_types[f.file_type] = file_types.get(f.file_type, 0) + 1

    return {
        "files": files,
        "file_types": file_types,
        "current_step": "scanner",
        "messages": [f"[scanner] 扫描到 {len(files)} 个文件，类型分布: {file_types}"],
    }


# B2: 启发式自动审批阈值与敏感扩展名集合
# 低风险条件：文件数 <= 50 且总大小 <= 100MB 且无敏感扩展名
_AUTO_APPROVE_MAX_FILES = 50
_AUTO_APPROVE_MAX_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB
# 敏感扩展名（命中任一即触发人工审批）：凭证/密钥/可执行脚本
_SENSITIVE_EXTENSIONS = {".env", ".key", ".pem", ".pfx", ".sh", ".bat"}


def _evaluate_approval_heuristic(files: list) -> tuple[bool, str]:
    """B2: 启发式评估扫描结果是否可自动通过。

    返回 (can_auto_approve, reason)。
    - can_auto_approve=True: 低风险，可自动通过
    - can_auto_approve=False: 高风险，需 interrupt 人工审批

    判定规则（全满足才放行）：
    1. 文件数 <= 50
    2. 文件总大小 <= 100MB
    3. 无敏感扩展名（.env/.key/.pem/.pfx/.sh/.bat）
    """
    file_count = len(files)
    if file_count > _AUTO_APPROVE_MAX_FILES:
        return False, f"文件数量 {file_count} 超过阈值 {_AUTO_APPROVE_MAX_FILES}"

    total_size = sum(getattr(f, "size", 0) for f in files)
    if total_size > _AUTO_APPROVE_MAX_SIZE_BYTES:
        size_mb = total_size / (1024 * 1024)
        return False, f"文件总大小 {size_mb:.1f}MB 超过阈值 100MB"

    # 收集所有文件扩展名（小写），检测敏感扩展名
    sensitive_hits = []
    for f in files:
        ext = getattr(f, "extension", "") or ""
        ext_lower = ext.lower()
        if ext_lower in _SENSITIVE_EXTENSIONS:
            sensitive_hits.append(f"{f.name}({ext_lower})")
    if sensitive_hits:
        return False, f"检测到敏感扩展名文件: {', '.join(sensitive_hits[:5])}"

    return True, f"低风险自动通过（{file_count} 文件，{total_size / (1024 * 1024):.1f}MB，无敏感扩展名）"


async def approval_node(state: AgentBuildState) -> dict:
    """HITL 审批节点：默认关闭 + 启发式自动审批。

    B2 改造逻辑（三层判定）：
    1. require_approval=False（默认）→ 直接通过，Canvas 不暂停
    2. require_approval=True + 启发式低风险 → 自动通过（文件≤50 且总大小≤100MB 且无敏感扩展名）
    3. require_approval=True + 启发式高风险 → interrupt 暂停等待人工审批

    使用 LangGraph 的 interrupt 机制暂停执行。
    调用方通过 Command(resume={"approved": True/False}) 恢复。
    """
    # 第一层：未启用审批，直接通过（默认路径，演示零中断）
    if not state.get("require_approval", False):
        return {
            "current_step": "approval",
            "messages": ["[approval] 未启用审批，直接通过"],
        }

    # 第二层：启用审批时，启发式评估是否可自动通过
    files = state.get("files", [])
    can_auto_approve, reason = _evaluate_approval_heuristic(files)
    if can_auto_approve:
        logger.info(f"[LangGraph:approval] 启发式自动通过: {reason}")
        return {
            "current_step": "approval",
            "status": "running",
            "messages": [f"[approval] 启发式自动通过: {reason}"],
        }

    # 第三层：高风险，interrupt 暂停等待人工审批
    logger.info(f"[LangGraph:approval] 触发人工审批: {reason}")
    from langgraph.types import interrupt

    files_info = [
        {"name": f.name, "type": f.file_type, "size": f.size}
        for f in files
    ]

    approval = interrupt({
        "step": "scanner",
        "message": f"扫描到 {len(files_info)} 个文件，{reason}，请人工确认是否继续",
        "files": files_info,
        "file_types": state.get("file_types", {}),
        "heuristic_reason": reason,
    })

    if isinstance(approval, dict) and approval.get("approved"):
        return {
            "current_step": "approval",
            "status": "running",
            "messages": ["[approval] 用户已批准，继续构建"],
        }
    else:
        return {
            "current_step": "approval",
            "status": "failed",
            "error": "用户取消了构建",
            "messages": [f"[approval] 用户拒绝: {approval}"],
        }


def make_parser_node(db_session_factory):
    """创建 parser 节点：解析每个文件 + 结构感知分块。

    需要独立 session 以便写入 File 记录。
    """
    # 延迟导入，避免循环依赖
    from app.services.document_processor import process_document
    from app.services.image_processor import process_image
    from app.services.video_processor import process_video
    from app.services.audio_transcriber import audio_transcriber

    async def parser_node(state: AgentBuildState) -> dict:
        logger.info("[LangGraph:parser] 开始解析文件并分块")
        agent_id = state["agent_id"]
        files = state.get("files", [])
        chunks_by_file = []
        total_chunks = 0

        # P1-6.4: 根据 Setup 向导索引策略选择分块参数
        from app.services.rag.chunker_service import ChunkerService

        index_strategy = state.get("index_strategy")
        if index_strategy == "precise":
            chunker = ChunkerService(chunk_size=400, chunk_overlap=40)
        elif index_strategy == "fast":
            chunker = ChunkerService(chunk_size=1200, chunk_overlap=120)
        else:
            chunker = chunker_service

        # BE-PER-01: 并发提取文件内容（CPU/IO 密集型），串行写入 DB
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FILE_PROCESSING)

        async def _extract_one(scanned_file):
            async with semaphore:
                try:
                    text = await _extract_text(
                        scanned_file.path, scanned_file.file_type,
                        process_document, process_image,
                        process_video, audio_transcriber,
                    )
                    if not text:
                        return scanned_file, None, None
                    chunks = await asyncio.to_thread(
                        chunker.chunk_text, text, file_type=scanned_file.file_type
                    )
                    return scanned_file, chunks, None
                except (httpx.HTTPError, ValueError, FileNotFoundError, RuntimeError, OSError) as e:
                    logger.error(f"解析文件失败 {scanned_file.name}: {e}", exc_info=True)
                    return scanned_file, None, ErrorCode.FILE_PROCESSING_FAILED
                except (TypeError, KeyError, AttributeError, PermissionError) as e:
                    errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                    logger.error(f"解析文件失败（未预期错误） {scanned_file.name}: {e}", exc_info=True)
                    return scanned_file, None, ErrorCode.FILE_PROCESSING_FAILED

        extraction_tasks = [asyncio.create_task(_extract_one(f)) for f in files]
        extraction_results = await asyncio.gather(*extraction_tasks, return_exceptions=True)

        async with db_session_factory() as db:
            for result in extraction_results:
                if isinstance(result, BaseException):
                    logger.error(f"提取任务异常: {result}", exc_info=True)
                    continue

                scanned_file, chunks, error = result
                file_record = File(
                    agent_id=agent_id,
                    original_name=scanned_file.name,
                    file_path=scanned_file.path,
                    file_size=scanned_file.size,
                    file_type=scanned_file.file_type,
                    status="processing",
                )
                db.add(file_record)
                await db.flush()

                if error:
                    file_record.status = "failed"
                    file_record.error_message = error
                elif not chunks:
                    file_record.status = "completed"
                    file_record.chunk_count = 0
                    file_record.vector_count = 0
                else:
                    chunks_by_file.append({
                        "file_id": str(file_record.id),
                        "file_name": scanned_file.name,
                        "file_path": scanned_file.path,
                        "file_type": scanned_file.file_type,
                        "chunks": chunks,
                    })
                    total_chunks += len(chunks)
                    file_record.status = "completed"
                    file_record.chunk_count = len(chunks)
                    file_record.vector_count = len(chunks)

            await db.commit()

        return {
            "chunks_by_file": chunks_by_file,
            "total_chunks": total_chunks,
            "current_step": "parser",
            "messages": [f"[parser] 解析完成，共 {total_chunks} 个分块"],
        }

    return parser_node


async def _extract_text(
    path: str,
    file_type: str,
    process_document_fn,
    process_image_fn,
    process_video_fn,
    audio_transcriber_inst,
) -> str:
    """从文件中提取文本（根据文件类型分发）。"""
    if file_type in ("document", "spreadsheet", "presentation"):
        return await process_document_fn(path)
    elif file_type == "image":
        result = await process_image_fn(path)
        return result.get("analysis", "")
    elif file_type == "video":
        result = await process_video_fn(path)
        return result.get("summary", "") + "\n" + result.get("transcription", "")
    elif file_type == "audio":
        return await audio_transcriber_inst.transcribe(path)
    elif file_type == "code":
        return await process_document_fn(path)
    return ""


def make_vectorizer_node(db_session_factory):
    """创建 vectorizer 节点：向量化存入 ChromaDB + 更新 Agent 统计。"""
    async def vectorizer_node(state: AgentBuildState) -> dict:
        logger.info("[LangGraph:vectorizer] 开始向量化")
        agent_id = state["agent_id"]
        chunks_by_file = state.get("chunks_by_file", [])

        # 新建 Agent 必须使用企业前缀集合；旧 agent_{id} 集合仅由查询双读与
        # 回填脚本兼容，不能继续作为写入目标，否则会造成多租户隔离策略分叉。
        vector_store = await VectorStoreService.create_prefixed(
            state["enterprise_id"], agent_id
        )

        # BE-PER-01 + P1-RAG: 使用 file_id 生成全局唯一 chunk_id，避免并发/增量冲突
        prepared = []
        for file_data in chunks_by_file:
            chunks = file_data.get("chunks", [])
            if not chunks:
                continue
            file_id = file_data.get("file_id")
            if not file_id:
                logger.warning("[LangGraph:vectorizer] 跳过无 file_id 的文件分块")
                continue
            chunk_ids = [f"{agent_id}_{file_id}_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "source": file_data["file_name"],
                    "file_path": file_data["file_path"],
                    "file_type": file_data["file_type"],
                    "chunk_index": i,
                }
                for i in range(len(chunks))
            ]
            prepared.append((chunks, metadatas, chunk_ids))

        # BE-PER-01: 并发写入 ChromaDB（已包装为异步方法，内部使用线程池）
        async def _add_one(args):
            chunks, metadatas, chunk_ids = args
            return await vector_store.add_documents(chunks, metadatas, chunk_ids)

        write_results = await asyncio.gather(
            *[_add_one(args) for args in prepared], return_exceptions=True
        )
        failures = [result for result in write_results if isinstance(result, BaseException)]
        if failures:
            # 不能把向量写入失败伪装成“构建完成”：这会让 Agent 的知识计数与
            # 实际可检索内容不一致。抛出后由耐久 Worker 标记失败并保留错误记录。
            first_failure = failures[0]
            raise RuntimeError(
                f"向量化写入失败（{len(failures)}/{len(write_results)} 批）: {first_failure}"
            ) from first_failure
        knowledge_count = sum(len(p[0]) for p in prepared)

        # 更新 Agent 统计
        async with db_session_factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if agent:
                agent.file_count = len(state.get("files", []))
                agent.knowledge_count = knowledge_count
                await db.commit()

        return {
            "vector_count": knowledge_count,
            "current_step": "vectorizer",
            "messages": [f"[vectorizer] 向量化完成，共 {knowledge_count} 个向量"],
        }

    return vectorizer_node


def make_builder_node(db_session_factory):
    """创建 builder 节点：生成 system_prompt + 默认 skills。"""
    # 复用 agent_builder 的逻辑
    from app.services.agent_builder import _create_default_skills, SYSTEM_PROMPT_TEMPLATE, _detect_file_types

    async def builder_node(state: AgentBuildState) -> dict:
        logger.info("[LangGraph:builder] 生成系统提示和技能")
        agent_id = state["agent_id"]

        async with db_session_factory() as db:
            # 查询企业名
            ent_result = await db.execute(
                select(Enterprise).where(Enterprise.id == state["enterprise_id"])
            )
            enterprise = ent_result.scalar_one_or_none()
            enterprise_name = enterprise.name if enterprise else "未知企业"

            # 生成 system_prompt
            file_count = len(state.get("files", []))
            knowledge_count = state.get("total_chunks", 0)
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                enterprise_name=enterprise_name,
                agent_name=state["name"],
                description=state.get("description", ""),
                file_count=file_count,
                knowledge_count=knowledge_count,
            )

            # 更新 Agent
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if agent:
                agent.system_prompt = system_prompt
                agent.status = "ready"

            # P1-6.4: 把 Setup 向导差异化参数持久化到 agent.config
            config = agent.config or {} if agent else {}
            if state.get("model"):
                config["model"] = state["model"]
            if state.get("index_strategy"):
                config["index_strategy"] = state["index_strategy"]
            if agent:
                agent.config = config

            # 创建默认 skills（优先使用 Setup 向导中用户选中的技能）
            files = state.get("files", [])
            file_types = _detect_file_types(files)
            selected_skills = state.get("skills")
            skills = await _create_default_skills(db, agent_id, file_types, selected_skills)

            await db.commit()

        return {
            "system_prompt": system_prompt,
            "skills_created": len(skills),
            "current_step": "builder",
            "messages": [f"[builder] 生成 {len(skills)} 个 skills，Agent 状态设为 ready"],
        }

    return builder_node


def make_tester_node(db_session_factory):
    """创建 tester 节点：测试 Agent 是否可用（轻量自检）。"""
    async def tester_node(state: AgentBuildState) -> dict:
        logger.info("[LangGraph:tester] 执行自检")
        agent_id = state["agent_id"]

        # 轻量自检：验证向量库可查询
        test_result = {
            "vector_store_ok": False,
            "agent_status_ok": False,
            "skills_count": state.get("skills_created", 0),
            "knowledge_count": state.get("vector_count", 0),
        }

        try:
            # BE-PER-04: 使用异步工厂方法获取 VectorStoreService
            vector_store = await VectorStoreService.create(f"agent_{agent_id}")
            count = await vector_store.count()
            test_result["vector_store_ok"] = count > 0
            test_result["vector_store_count"] = count
        except (chromadb.errors.ChromaError, ConnectionError, RuntimeError, OSError) as e:
            logger.error(f"向量库自检失败: {e}", exc_info=True)
            # BE-SEC-02: test_result 会通过 API 返回，避免暴露原始异常字符串
            test_result["vector_store_error"] = ErrorCode.FILE_VECTOR_WRITE_FAILED
            test_result["vector_store_error_type"] = type(e).__name__
        except (TypeError, ValueError, KeyError, AttributeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"向量库自检失败（未预期错误）: {e}", exc_info=True)
            test_result["vector_store_error"] = ErrorCode.FILE_VECTOR_WRITE_FAILED
            test_result["vector_store_error_type"] = type(e).__name__

        async with db_session_factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if agent:
                test_result["agent_status_ok"] = agent.status == "ready"
                test_result["agent_status"] = agent.status

        test_passed = test_result["vector_store_ok"] and test_result["agent_status_ok"]

        # B5 (O-11): 测试通过后自动启动文件监控，闭环知识库自动运维
        # - 仅在测试通过且有 folder_path 时启动
        # - file_watcher_service.is_available() 检查 watchdog 是否安装
        # - 失败仅记录 warning，不影响构建返回值（手动上传保留为兜底）
        if test_passed and state.get("folder_path"):
            try:
                from app.services.file_watcher import file_watcher_service
                if file_watcher_service.is_available():
                    started = await file_watcher_service.start_watching(
                        agent_id, state["folder_path"]
                    )
                    if started:
                        logger.info(
                            f"[LangGraph:tester] 已自动启动文件监控: agent={agent_id}, "
                            f"folder={state['folder_path']}"
                        )
                    else:
                        logger.warning(
                            f"[LangGraph:tester] 文件监控启动返回 False: agent={agent_id}"
                        )
                else:
                    logger.warning(
                        f"[LangGraph:tester] watchdog 未安装，跳过自动启动文件监控: agent={agent_id}"
                    )
            except (OSError, ValueError, RuntimeError) as e:
                logger.warning(f"[LangGraph:tester] 自动启动文件监控失败: {e}", exc_info=True)
            except (TypeError, KeyError, AttributeError) as e:
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                logger.warning(f"[LangGraph:tester] 自动启动文件监控未预期错误: {e}", exc_info=True)

        return {
            "test_result": test_result,
            "current_step": "tester",
            "status": "completed" if test_passed else "failed",
            "messages": [
                f"[tester] 自检{'通过' if test_passed else '失败'}: "
                f"向量={test_result['vector_store_ok']}, "
                f"状态={test_result['agent_status_ok']}"
            ],
        }

    return tester_node


# ============================================================
# 3. 错误处理节点
# ============================================================

def make_error_handler_node(db_session_factory):
    """错误处理节点：将 Agent 状态设为 error。"""
    async def error_handler_node(state: AgentBuildState) -> dict:
        agent_id = state.get("agent_id")
        error = state.get("error", "未知错误")
        logger.error(f"[LangGraph:error_handler] Agent 构建失败: {error}")

        if agent_id:
            async with db_session_factory() as db:
                result = await db.execute(select(Agent).where(Agent.id == agent_id))
                agent = result.scalar_one_or_none()
                if agent:
                    agent.status = "error"
                    await db.commit()

        return {
            "status": "failed",
            "messages": [f"[error_handler] 构建失败: {error}"],
        }

    return error_handler_node


# ============================================================
# 4. 图构建
# ============================================================

def build_agent_graph(db_session_factory, require_approval: bool = False):
    """构建 Agent 构建状态图。

    Args:
        db_session_factory: async_session_factory，用于创建独立 DB session
        require_approval: 是否启用 HITL 审批（默认 False）

    Returns:
        编译后的 LangGraph 应用（可 invoke/stream）
    """
    from langgraph.graph import StateGraph, START, END
    # P0-14 + P0-4 修复：使用 SqliteSaver（同步版）持久化 LangGraph 状态
    # 关键修复点：
    #   1. AsyncSqliteSaver.from_conn_string 返回 async context manager，
    #      只支持 __aenter__/__aexit__，同步 __enter__ 无法进入；改用同步 SqliteSaver
    #   2. 模块级单例避免每次 build_agent_graph 都新建连接（连接泄漏）
    checkpointer = _get_or_create_checkpointer()

    # 创建图
    graph = StateGraph(AgentBuildState)

    # 添加节点
    graph.add_node("planner", make_planner_node(db_session_factory))
    graph.add_node("scanner", scanner_node)
    graph.add_node("approval", approval_node)
    graph.add_node("parser", make_parser_node(db_session_factory))
    graph.add_node("vectorizer", make_vectorizer_node(db_session_factory))
    graph.add_node("builder", make_builder_node(db_session_factory))
    graph.add_node("tester", make_tester_node(db_session_factory))
    graph.add_node("error_handler", make_error_handler_node(db_session_factory))

    # 添加边
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "scanner")
    graph.add_edge("scanner", "approval")

    # 条件边：审批通过 → parser，拒绝 → error_handler
    graph.add_conditional_edges(
        "approval",
        lambda state: (
            "parser" if state.get("status") != "failed"
            else "error_handler"
        ),
        {"parser": "parser", "error_handler": "error_handler"},
    )

    graph.add_edge("parser", "vectorizer")
    graph.add_edge("vectorizer", "builder")
    graph.add_edge("builder", "tester")
    graph.add_edge("tester", END)
    graph.add_edge("error_handler", END)

    # 编译（带 checkpointer 实现状态持久化 + HITL）
    app = graph.compile(checkpointer=checkpointer)

    return app


# P0-4 修复：checkpointer 模块级单例，避免连接泄漏。
# AUD-16：解析逻辑已抽到 app/services/langgraph_checkpointer.py ——
# 依赖缺失或初始化失败时生产环境直接失败，不再静默回退 MemorySaver。
_checkpointer_instance = None


def _get_or_create_checkpointer():
    """获取或创建 LangGraph checkpointer 单例（见 langgraph_checkpointer 模块）。"""
    from app.services.langgraph_checkpointer import get_or_create_checkpointer

    return get_or_create_checkpointer()


# ============================================================
# 5. 便捷执行函数
# ============================================================

async def build_agent_via_graph(
    db_session_factory,
    enterprise_id: str,
    name: str,
    description: str,
    folder_path: str,
    require_approval: bool = False,
    thread_id: Optional[str] = None,
    model: Optional[str] = None,
    skills: Optional[list[str]] = None,
    index_strategy: Optional[str] = None,
) -> dict:
    """通过 LangGraph 构建 Agent。

    Args:
        db_session_factory: async_session_factory
        enterprise_id: 企业 ID
        name: Agent 名称
        description: Agent 描述
        folder_path: 知识库文件夹路径
        require_approval: 是否启用 HITL 审批
        thread_id: LangGraph 线程 ID（用于状态持久化恢复）
        model: 选择的 LLM 模型（Setup 向导传入）
        skills: 选择的技能 ID 列表（Setup 向导传入）
        index_strategy: 索引策略 balanced/precision/recall（Setup 向导传入）

    Returns:
        {
            "agent_id": str,
            "status": "completed" / "paused" / "failed",
            "test_result": dict,
            "messages": list[str],
        }
    """
    app = build_agent_graph(db_session_factory, require_approval=require_approval)

    initial_state: AgentBuildState = {
        "enterprise_id": enterprise_id,
        "name": name,
        "description": description,
        "folder_path": folder_path,
        "require_approval": require_approval,
        "model": model,
        "skills": skills,
        "index_strategy": index_strategy,
        "messages": [],
    }

    config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}

    # 执行图（异步）
    result = await app.ainvoke(initial_state, config=config)

    return {
        "agent_id": result.get("agent_id"),
        "status": result.get("status", "completed"),
        "test_result": result.get("test_result", {}),
        "messages": result.get("messages", []),
        "current_step": result.get("current_step"),
        # 返回 thread_id 供前端 Canvas 可视化轮询构建状态
        "thread_id": config["configurable"]["thread_id"],
    }


async def resume_agent_build(
    db_session_factory,
    thread_id: str,
    approval: dict,
) -> dict:
    """恢复被 HITL 中断的 Agent 构建。

    Args:
        db_session_factory: async_session_factory
        thread_id: 之前的线程 ID
        approval: {"approved": True/False, "comment": "..."}

    Returns:
        同 build_agent_via_graph 的返回值
    """
    from langgraph.types import Command

    app = build_agent_graph(db_session_factory, require_approval=True)
    config = {"configurable": {"thread_id": thread_id}}

    result = await app.ainvoke(Command(resume=approval), config=config)

    return {
        "agent_id": result.get("agent_id"),
        "status": result.get("status", "completed"),
        "test_result": result.get("test_result", {}),
        "messages": result.get("messages", []),
        "current_step": result.get("current_step"),
    }
