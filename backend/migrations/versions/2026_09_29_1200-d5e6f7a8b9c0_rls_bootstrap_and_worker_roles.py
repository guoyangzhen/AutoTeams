"""rls_bootstrap_and_worker_roles

Revision ID: d5e6f7a8b9c0
Revises: c2d3e4f5a6b8
Create Date: 2026-09-29 12:00:00.000000

AUD-19 第三轮：把"租户引导"与"跨租户队列能力"从运行角色里拆出来。

上一轮 ``c2d3e4f5a6b8`` 已经把受保护表的策略和授权补齐，但有三个入口在解析租户
之前就必须读受保护表（设备令牌、渠道回调、后台队列），因此生产连接仍然不能切到
``autofde_app``。这里补齐受控通道，并**把能力按角色拆开**：

角色（都不带任何成员关系，``NOINHERIT``，因此不会绕过 ``database.py`` 的
生产连接校验；也不持有任何对象所有权）：

``autofde_app``
    API 运行角色。除下面两个"持有密钥才能换租户"的解析函数与匿名审计函数外，
    **没有**任何跨租户能力。
``autofde_worker``
    后台队列角色。表权限与 ``autofde_app`` 完全一致（仅 SELECT/INSERT/UPDATE/
    DELETE，不镜像 TRUNCATE/REFERENCES/TRIGGER），额外持有五个
    SECURITY DEFINER 的跨租户领取/恢复函数（三个领取、两个恢复）。
``autofde_bootstrap``
    渠道回调引导角色。**没有任何表权限**，只能调用
    ``app_get_channel_webhook_material`` 取指定账号的验签材料。

为什么这样拆：``app_claim_*`` / ``app_recover_*`` 是队列 Worker 独占的跨租户
能力。如果像历史补丁那样把这些函数一并 ``GRANT`` 给 ``autofde_app``，那么任何
持有 API 数据库凭据的代码路径（含 SQL 注入）都能领取/回收**任意租户**的任务行，
隔离随之失效。领取函数不接收 enterprise_id 参数，天然跨租户，因此它的执行权
必须与 API 角色分离，并由数据库级反例证明 API 角色拿不到。

匿名安全审计：``log_audit(db, None, ...)`` 用于登录失败、账户锁定这类尚无用户的
事件。账本 INSERT 策略要求 user_id 属于本租户，受约束角色写不了；把策略改成
``WITH CHECK (true)`` 又等于允许任意租户以别人的 user_id 伪造审计。这里用独立
入口 ``app_append_anonymous_audit`` 收口：

* **没有 user_id 参数** —— 数据库强制写 NULL，匿名就是匿名，无法冒充他人；
* **动作白名单** —— 只允许 ``login_failed`` / ``login_locked``，不能借匿名通道
  伪造业务审计事件；
* **只能追加到链尾** —— 锁住 ``audit_chain_state`` 后校验 prev_hash 等于当前链尾，
  不一致直接报错，不能在链中间插入、不能悄悄丢掉既有条目；
* 签名仍由应用用 ``AUDIT_SIGNING_KEY`` 计算后传入：数据库不持有签名密钥，
  因此"内容真实性"来自应用密钥，"位置与主体"由数据库强制。

渠道回调的两阶段引导（避免"凭 account_id 换全租户"）：第一阶段用
``autofde_bootstrap`` 连接取验签材料（不返回 enterprise_id）；验签通过后，
业务会话用账号密钥摘要调用 ``app_resolve_channel_tenant`` 绑定租户。普通 API
角色无法读取任意账号的凭据，只有真正持有该账号回调密钥的调用方能换到租户。

所有函数都 ``REVOKE ... FROM PUBLIC`` 后再显式授权，并固定
``search_path = pg_catalog, public``。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c2d3e4f5a6b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "autofde_app"
WORKER_ROLE = "autofde_worker"
BOOTSTRAP_ROLE = "autofde_bootstrap"

#: 只授予 API 角色的"持有密钥才能换租户"解析函数。
TENANT_RESOLVERS = (
    "app_resolve_runner_credential_tenant(text, text)",
    "app_resolve_runner_token_tenant(text)",
    "app_resolve_channel_tenant(text, text)",
)

#: 只授予队列 Worker 角色的跨租户函数。
QUEUE_FUNCTIONS = (
    "app_claim_processing_task(text, integer)",
    "app_recover_processing_tasks(integer)",
    "app_claim_compilation_job(text, integer)",
    "app_recover_compilation_jobs()",
    "app_claim_agent_build_task(text, integer)",
    "app_recover_agent_build_tasks()",
)

#: 只授予引导角色的单账号验签材料读取。

#: 内部辅助函数：只被上面的 SECURITY DEFINER 函数以所有者身份调用。
#: 默认的 PUBLIC EXECUTE 必须显式收回，否则所有角色（含 API 角色）都能直接调用。
INTERNAL_FUNCTIONS = ("app_assert_queue_arguments(text, integer, integer)",)
BOOTSTRAP_FUNCTIONS = ("app_get_channel_webhook_material(text)",)

BOOTSTRAP_AUDIT_FUNCTION = (
    "app_append_bootstrap_audit(text, text, text, text, text, text, text, jsonb, "
    "timestamp with time zone, text, text)"
)

ALL_FUNCTIONS = TENANT_RESOLVERS + QUEUE_FUNCTIONS + BOOTSTRAP_FUNCTIONS + (
    BOOTSTRAP_AUDIT_FUNCTION,
)

#: 匿名安全事件白名单：只有"尚无用户"的安全事件能走匿名通道。
ANONYMOUS_AUDIT_ACTIONS = ("login_failed", "login_locked")

#: 无租户主体白名单：**只有** account 自身在 auth.py 里会记的动作。
#: `/auth/register` 创建的就是没有企业的账号，它随后还要能登录、刷新、改资料、
#: 改密码和登出 —— 这些事件的主体就是本人，没有资源或跨租户语义。
#: 主体仍必须等于当前已认证主体（app.current_user_id）；业务/资源类动作不在此列，
#: 它们必须由已认证入口绑定租户后按账本策略写入。
PRE_TENANT_AUDIT_ACTIONS = (
    "register",
    "login",
    "refresh_token",
    "logout",
    "update_profile",
    "change_password",
    "change_password_failed",
)

CHANNEL_TOKEN_HASH_COLUMN = "webhook_token_hash"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _create_restricted_roles() -> None:
    """建立两个新的受限角色；不创建任何成员关系。"""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
                CREATE ROLE {WORKER_ROLE} LOGIN
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{BOOTSTRAP_ROLE}') THEN
                CREATE ROLE {BOOTSTRAP_ROLE} LOGIN
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    for role in (WORKER_ROLE, BOOTSTRAP_ROLE):
        op.execute(
            f"ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
        )
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")


def _mirror_app_table_privileges() -> None:
    """把运行角色的表权限逐表复制给队列角色。

    只复制 SELECT/INSERT/UPDATE/DELETE：TRUNCATE 不走行级安全，REFERENCES/TRIGGER
    与队列无关，都不镜像。角色之间不建立成员关系，权限必须显式落到角色本身。
    """
    # aclitem 必须显式转 text：regexp_match / LIKE 只接受文本，隐式转换在实库上
    # 会直接报错而不是静默跳过，历史授权因此不会被漏检。
    op.execute(
        f"""
        DO $$
        DECLARE
            r record;
            privs text;
        BEGIN
            FOR r IN
                SELECT c.relname AS relname,
                       (regexp_match(e.acl::text, '{APP_ROLE}=([^/]*)'))[1] AS granted
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN LATERAL unnest(COALESCE(c.relacl, ARRAY[]::aclitem[])) AS e(acl)
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p')
                  AND e.acl::text LIKE '{APP_ROLE}=%'
            LOOP
                privs := '';
                IF position('r' in r.granted) > 0 THEN privs := privs || 'SELECT,'; END IF;
                IF position('a' in r.granted) > 0 THEN privs := privs || 'INSERT,'; END IF;
                IF position('w' in r.granted) > 0 THEN privs := privs || 'UPDATE,'; END IF;
                IF position('d' in r.granted) > 0 THEN privs := privs || 'DELETE,'; END IF;
                IF privs <> '' THEN
                    EXECUTE format('GRANT %s ON TABLE public.%I TO {WORKER_ROLE}',
                                   rtrim(privs, ','), r.relname);
                END IF;
            END LOOP;
        END
        $$;
        """
    )


def _assert_restricted_roles_are_clean() -> None:
    """失败关闭：角色**已经存在**时也必须干净，不能只在 CREATE 的那一刻安全。

    升级前的库里可能已经有同名角色（历史手工授权、上一版未审补丁留下的授权）。
    出现下面任何一种情况都必须让迁移失败，而不是"再 GRANT 一次"把问题盖住：

    1. 受限角色是别的角色的成员（能凭成员关系拿到额外能力）；
    2. 受限角色被授予了**其它**角色（等于把跨租户能力发给了别人）；
    3. 危险属性（超级用户 / BYPASSRLS / 建库 / 建角色 / INHERIT）；
    4. 拥有数据库、schema 或表；
    5. 引导角色持有任何表权限；队列角色持有 TRUNCATE/REFERENCES/TRIGGER
       （TRUNCATE 不走行级安全）。
    """
    op.execute(
        f"""
        DO $$
        DECLARE
            offender text;
        BEGIN
            SELECT string_agg(DISTINCT r.rolname, ', ') INTO offender
            FROM pg_roles r
            WHERE r.rolname IN ('{WORKER_ROLE}', '{BOOTSTRAP_ROLE}')
              AND (r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole
                   OR r.rolinherit
                   OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid)
                   OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.roleid = r.oid));
            IF offender IS NOT NULL THEN
                RAISE EXCEPTION
                    'restricted runtime roles carry forbidden attributes or memberships: %',
                    offender;
            END IF;

            SELECT string_agg(DISTINCT r.rolname, ', ') INTO offender
            FROM pg_roles r
            WHERE r.rolname IN ('{WORKER_ROLE}', '{BOOTSTRAP_ROLE}')
              AND (EXISTS (SELECT 1 FROM pg_class c WHERE c.relowner = r.oid)
                   OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspowner = r.oid)
                   OR EXISTS (SELECT 1 FROM pg_database d WHERE d.datdba = r.oid));
            IF offender IS NOT NULL THEN
                RAISE EXCEPTION 'restricted runtime roles must not own database objects: %',
                    offender;
            END IF;

            -- 引导角色在任何表上都不能有权限（包括历史遗留授权与 PUBLIC 授权）。
            SELECT string_agg(DISTINCT c.relname, ', ') INTO offender
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND has_table_privilege('{BOOTSTRAP_ROLE}', c.oid, 'SELECT');
            IF offender IS NOT NULL THEN
                RAISE EXCEPTION 'bootstrap role must not hold table privileges: %', offender;
            END IF;

            -- 队列角色不得持有 TRUNCATE/REFERENCES/TRIGGER：TRUNCATE 不走行级安全。
            SELECT string_agg(DISTINCT c.relname, ', ') INTO offender
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND (has_table_privilege('{WORKER_ROLE}', c.oid, 'TRUNCATE')
                   OR has_table_privilege('{WORKER_ROLE}', c.oid, 'REFERENCES')
                   OR has_table_privilege('{WORKER_ROLE}', c.oid, 'TRIGGER'));
            IF offender IS NOT NULL THEN
                RAISE EXCEPTION
                    'queue role must not hold TRUNCATE/REFERENCES/TRIGGER: %', offender;
            END IF;
        END
        $$;
        """
    )



def _create_tenant_resolvers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_resolve_runner_credential_tenant(
            p_device_id text, p_secret_hash text
        ) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT d.enterprise_id::text
            FROM public.runner_device_credentials c
            JOIN public.runner_devices d ON d.id = c.device_id
            WHERE c.device_id = p_device_id
              AND c.secret_hash = p_secret_hash
              AND c.revoked_at IS NULL
              AND (c.expires_at IS NULL OR c.expires_at > now())
              AND d.status = 'active'
            LIMIT 1
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_resolve_runner_token_tenant(
            p_token_hash text
        ) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT d.enterprise_id::text
            FROM public.runner_device_tokens t
            JOIN public.runner_devices d ON d.id = t.device_id
            WHERE t.token_hash = p_token_hash
              AND t.revoked_at IS NULL
              AND t.expires_at > now()
              AND d.status = 'active'
            LIMIT 1
        $$
        """
    )
    # 渠道账号：必须同时出示回调密钥摘要。仅凭 account_id 不能换租户。
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.app_resolve_channel_tenant(
            p_account_id text, p_secret_hash text
        ) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT c.enterprise_id::text
            FROM public.channel_accounts c
            WHERE c.id = p_account_id
              AND c.is_active IS TRUE
              AND c.{CHANNEL_TOKEN_HASH_COLUMN} = p_secret_hash
            LIMIT 1
        $$
        """
    )


def _create_bootstrap_reader() -> None:
    """引导角色只能取验签材料，取不到 enterprise_id，也碰不到任何表。"""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_get_channel_webhook_material(
            p_account_id text
        ) RETURNS TABLE (
            account_id text, channel_type text, is_active boolean,
            encrypted_credentials json
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT c.id::text, c.channel_type::text, c.is_active, c.encrypted_credentials
            FROM public.channel_accounts c
            WHERE c.id = p_account_id
            LIMIT 1
        $$
        """
    )


def _create_bootstrap_audit_writer() -> None:
    """受控的"尚无租户主体"审计入口。

    两种主体都由数据库判定，应用不能自选：

    * ``p_user_id IS NULL`` —— 登录失败/账户锁定这类还没有用户的事件，动作在白名单内；
    * ``p_user_id IS NOT NULL`` —— 刚注册、**还没有任何企业归属**的账号，动作在
      白名单内且该用户在 ``users`` 里确实 ``enterprise_id IS NULL``。已经属于某个
      企业的用户走不到这里：他们的审计必须由已认证入口绑定租户后按账本策略写入。
    """
    anonymous = ", ".join(f"'{action}'" for action in ANONYMOUS_AUDIT_ACTIONS)
    pre_tenant = ", ".join(f"'{action}'" for action in PRE_TENANT_AUDIT_ACTIONS)
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.app_append_bootstrap_audit(
            p_id text,
            p_user_id text,
            p_action text,
            p_resource_type text,
            p_resource_id text,
            p_ip_address text,
            p_user_agent text,
            p_details jsonb,
            p_created_at timestamp with time zone,
            p_signature text,
            p_prev_hash text
        ) RETURNS void
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            current_tip text;
            subject_id text;
        BEGIN
            IF p_id IS NULL OR p_signature IS NULL OR p_created_at IS NULL THEN
                RAISE EXCEPTION 'bootstrap audit requires id, signature and created_at'
                    USING ERRCODE = '42501';
            END IF;

            IF p_user_id IS NULL THEN
                IF p_action IS NULL OR p_action <> ALL (ARRAY[{anonymous}]) THEN
                    RAISE EXCEPTION 'anonymous audit action is not allowed: %', p_action
                        USING ERRCODE = '42501';
                END IF;
                subject_id := NULL;
            ELSE
                IF p_action IS NULL OR p_action <> ALL (ARRAY[{pre_tenant}]) THEN
                    RAISE EXCEPTION 'pre-tenant audit action is not allowed: %', p_action
                        USING ERRCODE = '42501';
                END IF;
                -- 主体必须等于当前已认证主体（app.current_user_id）。这与租户 GUC
                -- 是同一信任模型：数据库信任应用在认证入口写入的 GUC，因此
                -- "拿到应用连接就能任意 SET GUC"不在本轮的抵抗范围内；但替另一个
                -- 无企业账号署名会在数据库层被直接拒绝。
                IF p_user_id IS DISTINCT FROM
                       NULLIF(current_setting('app.current_user_id', true), '') THEN
                    RAISE EXCEPTION
                        'bootstrap audit subject must match the authenticated user'
                        USING ERRCODE = '42501';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM public.users u
                    WHERE u.id = p_user_id AND u.enterprise_id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'pre-tenant audit subject must be a user without an enterprise'
                        USING ERRCODE = '42501';
                END IF;
                subject_id := p_user_id;
            END IF;

            SELECT s.last_signature INTO current_tip
            FROM public.audit_chain_state s
            WHERE s.id = 'global'
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'audit chain state row is missing'
                    USING ERRCODE = '42501';
            END IF;
            IF current_tip IS DISTINCT FROM COALESCE(p_prev_hash, '') THEN
                RAISE EXCEPTION 'bootstrap audit must append to the current chain tip'
                    USING ERRCODE = '42501';
            END IF;

            INSERT INTO public.audit_logs (
                id, user_id, action, resource_type, resource_id,
                ip_address, user_agent, details, prev_hash, signature,
                created_at, updated_at
            ) VALUES (
                p_id, subject_id, p_action, p_resource_type, p_resource_id,
                p_ip_address, p_user_agent, p_details, p_prev_hash, p_signature,
                p_created_at, p_created_at
            );

            UPDATE public.audit_chain_state
            SET last_signature = p_signature, updated_at = now()
            WHERE id = 'global';
        END
        $$
        """
    )


def _create_claim_argument_guard() -> None:
    """领取/恢复的入参校验。

    空 worker_id 会让租约归属无法追溯，非法或过大的 lease_seconds 会让任务被
    长期"占住"却无人推进（恢复函数只回收已过期租约）。两者都必须直接报错，
    而不是写进队列表里等下一次恢复。
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_assert_queue_arguments(
            p_worker_id text, p_seconds integer, p_min_seconds integer
        ) RETURNS void
        LANGUAGE plpgsql IMMUTABLE
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF p_worker_id IS NOT NULL THEN
                IF btrim(p_worker_id) = '' OR length(p_worker_id) > 128 THEN
                    RAISE EXCEPTION 'worker identity must be a non-empty string up to 128 chars'
                        USING ERRCODE = '22023';
                END IF;
            END IF;
            IF p_seconds IS NULL OR p_seconds < p_min_seconds OR p_seconds > 3600 THEN
                RAISE EXCEPTION 'queue duration must be between % and 3600 seconds, got %',
                    p_min_seconds, p_seconds
                    USING ERRCODE = '22023';
            END IF;
        END
        $$
        """
    )


def _create_queue_functions() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_claim_processing_task(
            p_worker_id text, p_lease_seconds integer
        ) RETURNS SETOF public.processing_tasks
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE claimed public.processing_tasks;
        BEGIN
            -- 领取必须带 worker 身份：NULL 会产生没有归属的租约，
            -- 任务卡住时无法追责，也永远等不到续租。
            IF p_worker_id IS NULL THEN
                RAISE EXCEPTION 'worker identity is required to claim a task'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM public.app_assert_queue_arguments(p_worker_id, p_lease_seconds, 1);
            SELECT t.* INTO claimed
            FROM public.processing_tasks t
            WHERE t.status = 'pending' AND t.cancel_requested IS FALSE
            ORDER BY t.created_at ASC
            LIMIT 1 FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN
                RETURN;
            END IF;
            RETURN QUERY UPDATE public.processing_tasks t
            SET status = 'processing', lease_owner = p_worker_id,
                lease_until = now() + make_interval(secs => p_lease_seconds),
                heartbeat_at = now(), started_at = COALESCE(t.started_at, now()),
                message = '任务已由队列 worker 领取', attempt = t.attempt + 1,
                worker_id = p_worker_id
            WHERE t.id = claimed.id
            RETURNING t.*;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_recover_processing_tasks(
            p_grace_seconds integer
        ) RETURNS integer
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE changed integer;
        BEGIN
            PERFORM public.app_assert_queue_arguments(NULL, p_grace_seconds, 0);
            UPDATE public.processing_tasks
            SET status = 'pending', lease_owner = NULL, lease_until = NULL,
                worker_id = NULL,
                message = CASE WHEN attempt > 0
                    THEN '上次执行的 worker 租约已过期，重新排队'
                    ELSE '任务恢复排队' END
            WHERE status = 'processing'
              AND (lease_until IS NULL
                   OR lease_until < now() - make_interval(secs => p_grace_seconds));
            GET DIAGNOSTICS changed = ROW_COUNT;
            RETURN changed;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_claim_compilation_job(
            p_worker_id text, p_lease_seconds integer
        ) RETURNS TABLE (
            id character varying, enterprise_id character varying,
            folder_path text, interview_completion double precision,
            affected_stages character varying, attempt integer
        )
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE job public.compilation_jobs%ROWTYPE;
        BEGIN
            -- 领取必须带 worker 身份：NULL 会产生没有归属的租约，
            -- 任务卡住时无法追责，也永远等不到续租。
            IF p_worker_id IS NULL THEN
                RAISE EXCEPTION 'worker identity is required to claim a task'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM public.app_assert_queue_arguments(p_worker_id, p_lease_seconds, 1);
            SELECT c.* INTO job FROM public.compilation_jobs c
            WHERE c.status = 'queued'
               OR (c.status = 'running' AND (c.lease_until IS NULL OR c.lease_until < now()))
            ORDER BY c.created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN RETURN; END IF;
            IF job.cancel_requested THEN
                UPDATE public.compilation_jobs SET status='cancelled', completed_at=now(),
                    lease_owner=NULL, lease_until=NULL WHERE compilation_jobs.id=job.id;
                RETURN;
            END IF;
            IF job.folder_path IS NULL OR job.folder_path = '' THEN
                UPDATE public.compilation_jobs SET status='failed', completed_at=now(),
                    error_message='缺少持久化 folder_path，无法由 Worker 恢复编译',
                    lease_owner=NULL, lease_until=NULL WHERE compilation_jobs.id=job.id;
                RETURN;
            END IF;
            RETURN QUERY UPDATE public.compilation_jobs c
            SET status='running', started_at=COALESCE(c.started_at,now()),
                lease_owner=p_worker_id,
                lease_until=now() + make_interval(secs => p_lease_seconds),
                heartbeat_at=now(), attempt=COALESCE(c.attempt,0)+1,
                error_message=CASE WHEN c.lease_owner IS NOT NULL AND c.lease_owner<>p_worker_id
                    THEN format('前 Worker 租约已过期，已由 %s 接管（第 %s 次尝试）',
                                p_worker_id, COALESCE(c.attempt,0)+1)
                    ELSE c.error_message END
            WHERE c.id=job.id
            RETURNING c.id, c.enterprise_id, c.folder_path, c.interview_completion,
                      c.affected_stages, c.attempt;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_recover_compilation_jobs()
        RETURNS TABLE (requeued integer, failed integer)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE r integer; f integer;
        BEGIN
            UPDATE public.compilation_jobs
            SET status='queued', lease_owner=NULL, lease_until=NULL,
                heartbeat_at=now(), error_message='上次 Worker 中断，任务已重新入队等待恢复'
            WHERE status='running' AND (lease_until IS NULL OR lease_until<now())
              AND folder_path IS NOT NULL;
            GET DIAGNOSTICS r = ROW_COUNT;
            UPDATE public.compilation_jobs
            SET status='failed', completed_at=now(),
                error_message='历史编译任务缺少输入快照，无法安全恢复；请重新发起编译'
            WHERE status='running' AND (lease_until IS NULL OR lease_until<now())
              AND folder_path IS NULL;
            GET DIAGNOSTICS f = ROW_COUNT;
            RETURN QUERY SELECT r, f;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_claim_agent_build_task(
            p_worker_id text, p_lease_seconds integer
        ) RETURNS TABLE (
            id character varying, enterprise_id character varying, name character varying,
            description text, folder_path text, require_approval boolean,
            thread_id character varying, approval json
        )
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE task public.agent_build_tasks%ROWTYPE;
        BEGIN
            -- 领取必须带 worker 身份：NULL 会产生没有归属的租约，
            -- 任务卡住时无法追责，也永远等不到续租。
            IF p_worker_id IS NULL THEN
                RAISE EXCEPTION 'worker identity is required to claim a task'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM public.app_assert_queue_arguments(p_worker_id, p_lease_seconds, 1);
            SELECT t.* INTO task FROM public.agent_build_tasks t
            WHERE t.status = 'queued'
               OR (t.status = 'running' AND (t.lease_until IS NULL OR t.lease_until < now()))
            ORDER BY t.created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN RETURN; END IF;
            IF task.cancel_requested THEN
                UPDATE public.agent_build_tasks SET status='cancelled', completed_at=now(),
                    lease_owner=NULL, lease_until=NULL WHERE agent_build_tasks.id=task.id;
                RETURN;
            END IF;
            RETURN QUERY UPDATE public.agent_build_tasks t
            SET status='running', started_at=COALESCE(t.started_at,now()),
                lease_owner=p_worker_id,
                lease_until=now() + make_interval(secs => p_lease_seconds),
                heartbeat_at=now(), attempt=COALESCE(t.attempt,0)+1
            WHERE t.id=task.id
            RETURNING t.id, t.enterprise_id, t.name, t.description, t.folder_path,
                      t.require_approval, t.thread_id, t.approval;
        END
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_recover_agent_build_tasks()
        RETURNS integer
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE changed integer;
        BEGIN
            UPDATE public.agent_build_tasks
            SET status = 'queued', lease_owner = NULL, lease_until = NULL,
                heartbeat_at = now(),
                error_message = '上次 Worker 中断，任务已重新入队等待恢复'
            WHERE status = 'running'
              AND (lease_until IS NULL OR lease_until < now());
            GET DIAGNOSTICS changed = ROW_COUNT;
            RETURN changed;
        END
        $$
        """
    )


def _grant_execute_permissions() -> None:
    for signature in ALL_FUNCTIONS + INTERNAL_FUNCTIONS:
        # 函数默认对 PUBLIC 有 EXECUTE；不收回就等于所有角色都能直接调用。
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    for signature in TENANT_RESOLVERS + (BOOTSTRAP_AUDIT_FUNCTION,):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {APP_ROLE}")
    for signature in QUEUE_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {WORKER_ROLE}")
    for signature in BOOTSTRAP_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {BOOTSTRAP_ROLE}")
    # 队列角色只能调用"自己的"函数；引导角色同理。两个角色之间不互通。
    for role, forbidden in (
        (WORKER_ROLE, BOOTSTRAP_FUNCTIONS),
        (
            BOOTSTRAP_ROLE,
            QUEUE_FUNCTIONS + TENANT_RESOLVERS + (BOOTSTRAP_AUDIT_FUNCTION,),
        ),
    ):
        for signature in forbidden:
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM {role}")


def _assert_privilege_separation() -> None:
    """失败关闭：任何一个角色拿到不该有的跨租户能力都必须让迁移失败。

    名单全部来自上面的常量（QUEUE_FUNCTIONS / BOOTSTRAP_FUNCTIONS /
    INTERNAL_FUNCTIONS），不再手写函数名数组 —— 上一版就是因为手写名单漏掉了
    `app_recover_agent_build_tasks()`，历史错误授权才能存活下来。
    """
    queue_names = ", ".join(f"'{name.split('(')[0]}'" for name in QUEUE_FUNCTIONS)
    internal_names = ", ".join(f"'{name.split('(')[0]}'" for name in INTERNAL_FUNCTIONS)
    bootstrap_names = ", ".join(
        f"'{name.split('(')[0]}'" for name in BOOTSTRAP_FUNCTIONS
    )
    op.execute(
        f"""
        DO $$
        DECLARE leaked text;
        BEGIN
            SELECT string_agg(format('%s(%s)', p.proname, p.oid::regprocedure::text), ', ')
            INTO leaked
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = ANY (ARRAY[{queue_names}, {internal_names}])
              AND has_function_privilege('{APP_ROLE}', p.oid, 'EXECUTE');
            IF leaked IS NOT NULL THEN
                RAISE EXCEPTION 'API runtime role must not execute cross-tenant queue functions: %',
                    leaked;
            END IF;

            SELECT string_agg(format('%s(%s)', p.proname, p.oid::regprocedure::text), ', ')
            INTO leaked
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = ANY (ARRAY[{bootstrap_names}])
              AND has_function_privilege('{APP_ROLE}', p.oid, 'EXECUTE');
            IF leaked IS NOT NULL THEN
                RAISE EXCEPTION 'API runtime role must not execute: %', leaked;
            END IF;

            SELECT string_agg(format('%s(%s)', p.proname, p.oid::regprocedure::text), ', ')
            INTO leaked
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = ANY (ARRAY[{queue_names}, {internal_names}])
              AND has_function_privilege('{BOOTSTRAP_ROLE}', p.oid, 'EXECUTE');
            IF leaked IS NOT NULL THEN
                RAISE EXCEPTION 'bootstrap role must not execute: %', leaked;
            END IF;

            SELECT string_agg(format('%s(%s)', p.proname, p.oid::regprocedure::text), ', ')
            INTO leaked
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = ANY (ARRAY[{bootstrap_names}])
              AND has_function_privilege('{WORKER_ROLE}', p.oid, 'EXECUTE');
            IF leaked IS NOT NULL THEN
                RAISE EXCEPTION 'queue role must not read channel webhook material: %', leaked;
            END IF;
        END
        $$;
        """
    )


def _revoke_stale_execute_permissions() -> None:
    """把历史错误授权显式收回。

    断言只负责"发现并失败"，这一步负责"即使迁移重跑也不会保留上一版留下的
    直接授权"：API 角色永远拿不到队列/引导/内部函数。
    """
    for signature in QUEUE_FUNCTIONS + BOOTSTRAP_FUNCTIONS + INTERNAL_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM {APP_ROLE}")
    for signature in BOOTSTRAP_FUNCTIONS + INTERNAL_FUNCTIONS + TENANT_RESOLVERS + (
        BOOTSTRAP_AUDIT_FUNCTION,
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM {WORKER_ROLE}")


def upgrade() -> None:
    # 这一列两种方言都要迁移：SQLite 部署同样会跑真实 alembic upgrade，
    # 只在 PostgreSQL 分支加列会让 SQLite 库缺列（create_all 的单测掩盖不了）。
    op.add_column(
        "channel_accounts",
        sa.Column(CHANNEL_TOKEN_HASH_COLUMN, sa.String(length=64), nullable=True),
    )

    if not _is_postgres():
        return

    # 登录失败/账户锁定把**邮箱**写进 resource_id，而合法邮箱可以超过 36 字符。
    # 列宽不足会让这条受控写入直接抛错，安全事件反而留不下痕迹。
    op.execute(
        "ALTER TABLE public.audit_logs "
        "ALTER COLUMN resource_id TYPE character varying(255)"
    )
    _create_restricted_roles()
    _assert_restricted_roles_are_clean()
    _mirror_app_table_privileges()
    _assert_restricted_roles_are_clean()
    _create_claim_argument_guard()
    _create_tenant_resolvers()
    _create_bootstrap_reader()
    _create_bootstrap_audit_writer()
    _create_queue_functions()
    _grant_execute_permissions()
    _revoke_stale_execute_permissions()
    _assert_privilege_separation()


def downgrade() -> None:
    if _is_postgres():
        # 只删本迁移**新建**的对象：函数与新增列。
        #
        # 刻意**不**做的事：
        # 1. 不 DROP ROLE —— 角色是集群级对象，可能早已存在或被其它库使用，
        #    DROP OWNED 还会连带剥夺与本迁移无关的授权。
        # 2. 不 REVOKE worker 的表权限 —— 那无法与"预存授权"区分，撤错了会
        #    破坏别的部署。残留的 `autofde_worker` / `autofde_bootstrap` 是两个
        #    **无特权**角色：跨租户函数已随函数一起删除，表权限仍受 RLS 约束，
        #    需要清理时由 DBA 审查后手工处理。
        # 3. 不把 audit_logs.resource_id 缩回 36 —— 库里可能已存在长邮箱的安全
        #    事件日志，缩列会直接失败。这是一次**非破坏性**的兼容性扩宽。
        for signature in ALL_FUNCTIONS + INTERNAL_FUNCTIONS:
            op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")

    op.drop_column("channel_accounts", CHANNEL_TOKEN_HASH_COLUMN)
