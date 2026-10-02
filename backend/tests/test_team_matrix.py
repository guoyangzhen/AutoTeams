"""AutoTeams 4.0 矩阵团队协同、3轮淘汰竞标与黑板单元测试。"""
import pytest
import uuid
from app.models.enterprise import Enterprise
from app.services.matrix.bidding_engine import BiddingEngine, BidEvaluationResult
from app.services.matrix.blackboard_service import (
    post_blackboard_entry,
    list_blackboard_entries,
)
from app.services.matrix.team_service import (
    create_workgroup_team,
    get_workgroup_team,
    list_workgroup_teams,
    create_matrix_task,
    list_matrix_tasks,
    submit_candidate_bid,
    award_and_start_task,
    submit_deliverable,
    review_deliverable,
)


def test_bidding_engine_hp_degradation_and_elimination():
    """测试 3 轮竞标 HP 扣减与淘汰机制。"""
    # 候选人 1: 优秀表现，每轮 9 分 -> 扣减 (10-9)*3 = 3 HP
    r1 = BiddingEngine.evaluate_bid_round(
        candidate_profile_id="emp-alice",
        round_num=1,
        statement="技术方案完善，架构清晰",
        current_hp=100.0,
        score=9.0,
        score_rationale="表现出色",
    )
    assert r1.remaining_hp == 97.0
    assert not r1.is_eliminated

    # 候选人 2: 较差表现，初始 15 HP，单轮 4 分 -> 扣减 (10-4)*3 = 18 HP -> HP 降至 0 (淘汰)
    r2 = BiddingEngine.evaluate_bid_round(
        candidate_profile_id="emp-bob",
        round_num=1,
        statement="方案不完整",
        current_hp=15.0,
        score=4.0,
        score_rationale="技能不匹配",
    )
    assert r2.remaining_hp == 0.0
    assert r2.is_eliminated is True

    # 终局裁决：emp-alice 胜出，emp-bob 被淘汰
    winner_id = BiddingEngine.adjudicate_winner([
        {"candidate_profile_id": "emp-alice", "final_hp": r1.remaining_hp, "performance_score": 95.0},
        {"candidate_profile_id": "emp-bob", "final_hp": r2.remaining_hp, "performance_score": 70.0},
    ])
    assert winner_id == "emp-alice"


def test_bidding_engine_tie_breaking():
    """测试候选人加权打分裁决最优选择。"""
    # 候选人 1: HP 95, 历史评级 90 -> 95*0.7 + 90*0.3 = 66.5 + 27 = 93.5
    # 候选人 2: HP 90, 历史评级 99 -> 90*0.7 + 99*0.3 = 63.0 + 29.7 = 92.7
    winner = BiddingEngine.adjudicate_winner([
        {"candidate_profile_id": "emp-c1", "final_hp": 95.0, "performance_score": 90.0},
        {"candidate_profile_id": "emp-c2", "final_hp": 90.0, "performance_score": 99.0},
    ])
    assert winner == "emp-c1"


@pytest.mark.asyncio
async def test_blackboard_lifecycle(db_session):
    """测试共享黑板（Blackboard）知识沉淀与引用溯源。"""
    team_id = f"team-{uuid.uuid4().hex[:6]}"
    task_id = f"task-{uuid.uuid4().hex[:6]}"

    # 1. 写入黑板结构化产物
    entry = await post_blackboard_entry(
        db=db_session,
        team_id=team_id,
        topic="微服务 API 规范",
        content="包含 /api/v1/users 与 /api/v1/orders 接口定义",
        source_profile_id="emp-architect",
        source_task_id=task_id,
        citations=[{"doc_id": "rfc-101", "title": "系统设计说明书"}],
        is_pinned=True,
    )
    assert entry.id is not None
    assert entry.topic == "微服务 API 规范"
    assert entry.is_pinned is True

    # 2. 查询黑板内容
    entries = await list_blackboard_entries(db_session, team_id=team_id)
    assert len(entries) == 1
    assert entries[0].topic == "微服务 API 规范"

    # 3. 按置顶过滤与主题过滤
    pinned = await list_blackboard_entries(db_session, team_id=team_id, pinned_only=True)
    assert len(pinned) == 1

    by_topic = await list_blackboard_entries(db_session, team_id=team_id, topic="微服务 API 规范")
    assert len(by_topic) == 1

    empty = await list_blackboard_entries(db_session, team_id=team_id, topic="不存在的主题")
    assert len(empty) == 0


@pytest.mark.asyncio
async def test_team_matrix_task_lifecycle(db_session):
    """测试矩阵团队创建、任务竞标认领、交付与评审完整闭环。"""
    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    leader_id = f"lead-{uuid.uuid4().hex[:4]}"
    dev1_id = f"dev1-{uuid.uuid4().hex[:4]}"
    dev2_id = f"dev2-{uuid.uuid4().hex[:4]}"

    # 0. 先创建所属企业实体
    enterprise = Enterprise(id=ent_id, name="先锋智能科技")
    db_session.add(enterprise)
    await db_session.commit()

    # 1. 创建工作组团队
    team = await create_workgroup_team(
        db=db_session,
        enterprise_id=ent_id,
        name="智能交付先锋队",
        leader_profile_id=leader_id,
        member_profile_ids=[dev1_id, dev2_id],
        description="负责核心业务系统的智能开发与测试交付",
    )
    assert team.id is not None
    assert team.name == "智能交付先锋队"
    assert team.leader_profile_id == leader_id
    assert dev1_id in team.member_profile_ids

    # 2. 团队查询
    teams = await list_workgroup_teams(db_session, enterprise_id=ent_id)
    assert len(teams) >= 1

    # 3. 创建团队任务
    task = await create_matrix_task(
        db=db_session,
        team_id=team.id,
        title="开发企业微信回调网关",
        description="按企业级安全规范对接企微消息回调协议并支持重试",
        priority=2,
    )
    assert task.status == "pending"
    assert task.priority == 2

    # 4. 候选人竞标提交
    bid1 = await submit_candidate_bid(
        db=db_session,
        task_id=task.id,
        team_id=team.id,
        candidate_profile_id=dev1_id,
        round_num=1,
        statement="我熟悉企业微信 API，预计 2 小时交付",
        score=9.2,
        score_rationale="技术方案清晰，经验丰富",
    )
    assert bid1.current_hp == round(100.0 - (10.0 - 9.2) * 3.0, 2)  # 97.6

    bid2 = await submit_candidate_bid(
        db=db_session,
        task_id=task.id,
        team_id=team.id,
        candidate_profile_id=dev2_id,
        round_num=1,
        statement="也可以完成，但缺乏企微签名验证经验",
        score=6.0,
        score_rationale="缺少关键经验，需较多联调时间",
    )
    assert bid2.current_hp == round(100.0 - (10.0 - 6.0) * 3.0, 2)  # 88.0

    # 5. 授标并启动任务
    awarded_task = await award_and_start_task(
        db=db_session,
        task_id=task.id,
        team_id=team.id,
        winner_profile_id=dev1_id,
    )
    assert awarded_task.status == "in_progress"
    assert awarded_task.assignee_profile_id == dev1_id

    # 6. 提交交付物（Deliverable）
    delivered_task = await submit_deliverable(
        db=db_session,
        task_id=task.id,
        team_id=team.id,
        report_data={"git_branch": "feat/wecom-gateway", "tests_passed": 12},
    )
    assert delivered_task.status == "review"

    # 7. 负责人验收评审
    reviewed_task = await review_deliverable(
        db=db_session,
        task_id=task.id,
        team_id=team.id,
        approved=True,
        feedback={"comment": "代码规范符合要求，测试全量通过，予以验收"},
    )
    assert reviewed_task.status == "done"
    assert reviewed_task.review_feedback.get("comment") == "代码规范符合要求，测试全量通过，予以验收"
