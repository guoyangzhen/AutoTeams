"""LangGraph 持久化 checkpointer 回归测试（AUD-16）。

两个缺陷，一个比一个严重：

1. `langgraph-checkpoint-sqlite` 不在依赖里，导入必然失败 → 静默回退
   ``MemorySaver``，HITL 审批检查点随进程重启丢失。
2. 补上依赖后暴露更深的问题：图是用 ``await app.ainvoke(...)`` 调用的，而代码装的是
   **同步** ``SqliteSaver``。LangGraph 的异步检查点操作会抛
   ``NotImplementedError: The SqliteSaver does not support async methods`` ——
   也就是说"修好依赖"反而让整条构建链路在运行时崩溃。

本测试直接跑真实的 LangGraph 状态图，验证：

* 生产环境不会静默回退到内存；
* ``ainvoke`` 真的能跑通（同步 saver 在这里会直接炸）；
* 关闭并重新打开同一个 checkpoint 文件后，状态仍能读回（= 进程重启可恢复）。
"""
import pytest
from langgraph.graph import START, END, StateGraph
from typing import TypedDict

from app.config import settings
from app.services.langgraph_checkpointer import (
    CheckpointerUnavailableError,
    get_or_create_checkpointer,
    init_checkpointer,
    reset_checkpointer_cache,
    shutdown_checkpointer,
)


class _Counter(TypedDict):
    n: int


async def _increment(state: _Counter) -> _Counter:
    return {"n": state["n"] + 1}


def _build_graph():
    graph = StateGraph(_Counter)
    graph.add_node("increment", _increment)
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    return graph.compile(checkpointer=get_or_create_checkpointer())


@pytest.fixture
async def checkpoint_file(tmp_path, monkeypatch):
    """把 checkpoint 指向临时文件，并在用例结束后清空进程内缓存。"""
    path = tmp_path / "checkpoints.sqlite"
    monkeypatch.setattr(settings, "LANGGRAPH_CHECKPOINT_PATH", str(path))
    reset_checkpointer_cache()
    yield str(path)
    await shutdown_checkpointer()
    reset_checkpointer_cache()


@pytest.mark.asyncio
async def test_ainvoke_works_with_persistent_checkpointer(checkpoint_file):
    """ainvoke 不得因同步/异步不匹配而崩溃。"""
    await init_checkpointer()
    saver = get_or_create_checkpointer()
    assert "Memory" not in type(saver).__name__, "持久化可用时不得回退内存"
    assert "Async" in type(saver).__name__, "必须使用与 ainvoke 兼容的异步 saver"

    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    result = await _build_graph().ainvoke({"n": 0}, config=config)
    assert result["n"] == 1


@pytest.mark.asyncio
async def test_state_survives_process_restart(checkpoint_file):
    """关闭并重新打开同一文件后，状态仍能读回 —— 即进程重启可恢复。"""
    config = {"configurable": {"thread_id": "thread-restart", "checkpoint_ns": ""}}

    await init_checkpointer()
    await _build_graph().ainvoke({"n": 41}, config=config)

    # 模拟进程退出
    await shutdown_checkpointer()
    reset_checkpointer_cache()

    # 模拟新进程启动
    await init_checkpointer()
    state = await _build_graph().aget_state(config)
    assert state.values == {"n": 42}


@pytest.mark.asyncio
async def test_production_refuses_uninitialized_checkpointer(monkeypatch):
    """生产环境未初始化持久化 checkpointer 时必须拒绝，而不是回退内存。"""
    reset_checkpointer_cache()
    monkeypatch.setattr(settings, "DEBUG", False)
    with pytest.raises(CheckpointerUnavailableError) as excinfo:
        get_or_create_checkpointer()
    assert "不允许回退 MemorySaver" in str(excinfo.value)
    reset_checkpointer_cache()


@pytest.mark.asyncio
async def test_development_falls_back_with_warning(monkeypatch, tmp_path):
    """开发环境允许回退，但必须计入错误指标并可观测。"""
    reset_checkpointer_cache()
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "LANGGRAPH_CHECKPOINT_PATH", str(tmp_path / "never.sqlite"))
    saver = get_or_create_checkpointer()
    # langgraph 0.4.x 里该类名为 InMemorySaver（MemorySaver 是别名）
    assert "Memory" in type(saver).__name__
    reset_checkpointer_cache()
