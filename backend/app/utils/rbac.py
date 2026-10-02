"""RBAC（基于角色的访问控制）依赖。

用法：
    from app.utils.rbac import require_admin

    @router.delete("/{id}")
    async def delete_item(
        current_user: User = Depends(require_admin),
    ):
        ...

S9: require_admin_or_owner 用于资源所有者或管理员可操作的端点：
    @router.delete("/{item_id}")
    async def delete_item(
        item_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        item = await load_item(db, item_id)
        await require_admin_or_owner(current_user, item.owner_id)
        ...
"""
from fastapi import Depends, HTTPException
from app.models.user import User
from app.utils.security import get_current_user
from app.utils.error_codes import ErrorCode


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求当前用户是管理员（role == 'admin'）。

    安全修复（C1 二次审计）：移除"`enterprise_id is None` 自动放行"逻辑。
    历史上把所有 `enterprise_id is None` 的用户视为超级管理员，
    但 `/auth/register` 是公开端点且创建 `enterprise_id=None, role="member"` 的用户，
    任何注册用户都能借此绕过 admin 权限检查、访问审计日志等敏感端点。

    修复后：
    - 系统超管 = `enterprise_id is None` + `role == "admin"`
    - 企业管理员 = `enterprise_id is not None` + `role == "admin"`
    - 其他一律 403
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    return current_user


async def require_admin_or_owner(
    current_user: User,
    resource_owner_id: str,
) -> User:
    """S9: 要求当前用户是资源所有者或管理员（含超级管理员）。

    用于资源所有者可自行操作、管理员可代为操作的场景，例如：
    - 用户删除自己创建的 Agent
    - 用户修改自己的个人资料
    - 管理员代为删除任意用户的资源

    用法（作为普通函数调用，而非 Depends）：
        @router.delete("/{agent_id}")
        async def delete_agent(
            agent_id: str,
            current_user: User = Depends(get_current_user),
            db: AsyncSession = Depends(get_db),
        ):
            agent = await load_agent(db, agent_id)
            await require_admin_or_owner(current_user, agent.owner_id)
            ...

    判定顺序：
    1. 管理员（role == 'admin'，含系统超管与企业管理员）→ 放行
    2. 资源所有者（user.id == resource_owner_id）→ 放行
    3. 其他 → 403

    安全修复（C1 二次审计）：与 require_admin 一致，移除
    "`enterprise_id is None` 自动放行"逻辑，避免公开注册端点创建的
    `enterprise_id=None, role="member"` 用户绕过权限检查。
    """
    # 管理员放行（含系统超管与企业管理员）
    if current_user.role == "admin":
        return current_user
    # 资源所有者放行
    if str(current_user.id) == str(resource_owner_id):
        return current_user
    raise HTTPException(
        status_code=403,
        detail=ErrorCode.FORBIDDEN,
    )
