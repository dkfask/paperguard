"""用户认证 API: 注册 / 登录 / 登出 / 当前用户。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth import (
    _save_user_metadata,
    exchange_mp_code,
    mini_program_bind,
    mini_program_login,
    mini_program_unbind,
    mp_bind_email,
    mp_register,
    public_user,
    report_mp_subscription,
    send_email_code,
    complete_first_login,
    change_password,
    current_user,
    login_user,
    logout_user,
    register_user,
    require_admin,
    seed_admin,
)
from app.db import Store
from app.services.password_recovery import reset_password
from app.services.role_access import BUSINESS_ROLES

router = APIRouter(prefix="/api/auth")


def get_store() -> Store:
    store = Store()
    seed_admin(store)   # 懒加载: 保证内置管理员始终存在
    return store


class RegisterRequest(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = ""
    password: str
    display_name: str = ""
    role: str = "user"
    email: str = ""
    email_code: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class MiniProgramLoginRequest(BaseModel):
    code: str


class MiniProgramBindRequest(BaseModel):
    bind_ticket: str
    username: str
    password: str


class MiniProgramUnbindRequest(BaseModel):
    password: str


@router.post("/register", status_code=201)
def register(req: RegisterRequest, store: Store = Depends(get_store)) -> dict:
    """注册账号 (username 缺省时由邮箱派生; SMTP 已配置时需邮箱验证码)。"""
    # Public registration is always a restricted onboarding identity. The
    # request role is presentation metadata only; administrators assign the
    # approved business role through authenticated management flows.
    return register_user(store, req.username, req.password, req.display_name, "user",
                         email=req.email, email_code=req.email_code)


class RoleCertificationRequest(BaseModel):
    role: str
    organization_context: dict[str, str] = Field(default_factory=dict)


def _require_unselected_user(user: dict) -> None:
    """学校身份认证只服务「尚未选定学校角色」的 user。

    缺这道前置条件时，认证自助流程对任何已登录角色都是敞开的：
      - POST 走 role=user 分支会**立刻**把正式教师/学生的 role 覆写成 user、
        status 置 active、application 置 approved，全程不经过任何审批；
      - POST 走 role=student/teacher 分支会把调用者塞进管理员审批队列，
        而审批端点会把 target["role"] 直接改成 requested_role，
        于是「申请更高角色」这条路对已获批账号同样成立；
      - GET 不做作用域过滤，会把全量学校/学院组织树吐给任何登录用户。
    认证是一次性动作，角色一旦是 student/teacher/admin 就不该再进这条流程。
    """
    if user.get("role") != "user":
        raise HTTPException(status_code=403, detail="当前角色无需学校身份认证")


@router.get("/role-certification")
def role_certification_options(user: dict = Depends(current_user), store: Store = Depends(get_store)) -> dict:
    _require_unselected_user(user)
    units = [
        {"id": unit.id, "name": unit.name, "type": unit.type.value, "parent_id": unit.parent_id}
        for unit in store.list_organization_units()
        if unit.type.value in {"school", "college"}
    ]
    return {
        "role": user["role"],
        "requested_role": user.get("requested_role"),
        "requested_organization_context": user.get("requested_organization_context") or {},
        "application_status": user.get("role_application_status", "unselected"),
        "organizations": units,
    }


@router.post("/role-certification")
def submit_role_certification(req: RoleCertificationRequest,
                              user: dict = Depends(current_user),
                              store: Store = Depends(get_store)) -> dict:
    _require_unselected_user(user)
    role = req.role.strip().lower()
    if role not in {"student", "teacher"}:
        raise HTTPException(status_code=422, detail="当前仅开放学生或教师身份申请")
    context = {str(key): str(value) for key, value in req.organization_context.items() if value}
    if "school_id" not in context:
        raise HTTPException(status_code=422, detail="学生或教师认证必须选择学校")
    school = store.get_organization_unit(context["school_id"])
    if school is None or school.type.value != "school":
        raise HTTPException(status_code=422, detail="选择的学校组织不存在")
    if "college_id" in context:
        college = store.get_organization_unit(context["college_id"])
        if college is None or college.type.value != "college" or college.parent_id != school.id:
            raise HTTPException(status_code=422, detail="选择的学院不属于该学校")
    if any(key not in {"school_id", "college_id"} for key in context):
        raise HTTPException(status_code=422, detail="选择的组织不存在")
    user["requested_role"] = role
    user["requested_organization_context"] = context
    user["role_application_status"] = "pending"
    _save_user_metadata(store, user, (
        "role", "status", "organization_context", "requested_role",
        "requested_organization_context", "role_application_status",
    ), actor=user)
    return public_user(store.get_user(user["id"]))


class EmailCodeRequest(BaseModel):
    email: str
    purpose: str = "register"


@router.post("/email/code")
def request_email_code(req: EmailCodeRequest, store: Store = Depends(get_store)) -> dict:
    """发送邮箱验证码 (SMTP 未配置返回 503 EMAIL_NOT_CONFIGURED)。"""
    return send_email_code(store, req.email, purpose=req.purpose)


@router.post("/login")
def login(req: LoginRequest, store: Store = Depends(get_store)) -> dict:
    """登录, 返回 {token, user}。"""
    return login_user(store, req.username, req.password)


class PasswordResetRequest(BaseModel):
    model_config = {"extra": "forbid"}
    email: str
    email_code: str
    new_password: str


@router.post("/reset-password")
def reset_user_password(req: PasswordResetRequest, store: Store = Depends(get_store)) -> dict:
    """已验证的绑定邮箱确认身份；不签发会话，旧会话全部撤销。"""
    reset_password(store, req.email, req.email_code, req.new_password)
    return {"ok": True}


@router.post("/mp/login")
async def mini_program_login_endpoint(req: MiniProgramLoginRequest,
                                      store: Store = Depends(get_store)) -> dict:
    if not req.code.strip():
        raise HTTPException(status_code=422, detail={"code": "MP_CODE_REQUIRED", "message": "微信登录凭证不能为空"})
    openid = await exchange_mp_code(req.code.strip())
    return mini_program_login(store, openid)


@router.post("/mp/bind")
def mini_program_bind_endpoint(req: MiniProgramBindRequest,
                               store: Store = Depends(get_store)) -> dict:
    if not req.bind_ticket.strip():
        raise HTTPException(status_code=422, detail={"code": "MP_BIND_TICKET_REQUIRED", "message": "绑定票据不能为空"})
    return mini_program_bind(store, req.bind_ticket.strip(), req.username, req.password)


class MiniProgramRegisterRequest(BaseModel):
    model_config = {"extra": "forbid"}
    bind_ticket: str


@router.post("/mp/register", status_code=201)
def mini_program_register_endpoint(req: MiniProgramRegisterRequest,
                                   store: Store = Depends(get_store)) -> dict:
    """微信一键注册: 以 openid 直接创建学生账号 (随后绑定邮箱设置桌面端密码)。"""
    if not req.bind_ticket.strip():
        raise HTTPException(status_code=422, detail={"code": "MP_BIND_TICKET_REQUIRED", "message": "绑定票据不能为空"})
    return mp_register(store, req.bind_ticket.strip())


@router.post("/mp/bind-email")
def mini_program_bind_email_endpoint(req: MiniProgramBindEmailRequest,
                                     user: dict = Depends(current_user),
                                     store: Store = Depends(get_store)) -> dict:
    """为当前小程序账号绑定邮箱并设置桌面端登录密码。"""
    return {"user": mp_bind_email(store, user, req.email, req.email_code, req.password)}


class MiniProgramBindEmailRequest(BaseModel):
    email: str
    email_code: str = ""
    password: str


class FirstLoginCompleteRequest(BaseModel):
    email: str
    email_code: str
    new_password: str


@router.post("/first-login/complete")
def first_login_complete(req: FirstLoginCompleteRequest,
                         user: dict = Depends(current_user),
                         store: Store = Depends(get_store)) -> dict:
    """学校统一注册学生首次绑定邮箱并完成初始密码设置。"""
    return complete_first_login(store, user, req.email, req.email_code, req.new_password)


@router.post("/logout")
def logout(authorization: str | None = Header(default=None), store: Store = Depends(get_store)) -> dict:
    if authorization and authorization.startswith("Bearer "):
        logout_user(store, authorization.removeprefix("Bearer ").strip())
    return {"status": "ok"}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_user_password(req: ChangePasswordRequest,
                         user: dict = Depends(current_user),
                         store: Store = Depends(get_store)) -> dict:
    change_password(store, user, req.current_password, req.new_password)
    return {"status": "ok", "message": "密码已更新, 请重新登录"}


@router.post("/mp/unbind")
def mini_program_unbind_endpoint(req: MiniProgramUnbindRequest,
                                 authorization: str | None = Header(default=None),
                                 user: dict = Depends(current_user),
                                 store: Store = Depends(get_store)) -> dict:
    mini_program_unbind(store, user, req.password)
    if authorization and authorization.startswith("Bearer "):
        logout_user(store, authorization.removeprefix("Bearer ").strip())
    return {"status": "ok"}


class MiniProgramSubscribeRequest(BaseModel):
    template_id: str
    accepted: bool


@router.post("/mp/subscribe")
def mini_program_subscribe_endpoint(req: MiniProgramSubscribeRequest,
                                    user: dict = Depends(current_user),
                                    store: Store = Depends(get_store)) -> dict:
    """记录用户在小程序内对订阅消息模板的授权结果(一次性配额)。"""
    template_id = req.template_id.strip()
    if not template_id:
        raise HTTPException(status_code=422, detail={"code": "MP_TEMPLATE_REQUIRED", "message": "模板 ID 不能为空"})
    remaining = report_mp_subscription(store, user, template_id, req.accepted)
    return {"ok": True, "remaining": remaining}


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    """当前登录用户。"""
    return public_user(user)


class UserRoleStatusUpdate(BaseModel):
    role: str | None = None
    status: str | None = None


class RoleCertificationDecision(BaseModel):
    decision: str


@router.get("/admin/users")
def list_admin_users(admin: dict = Depends(require_admin),
                     store: Store = Depends(get_store)) -> list[dict]:
    """管理员查看全站用户列表。"""
    users = store.list_users()
    return [public_user(u) for u in users]


@router.put("/admin/users/{user_id}/role-status")
def update_user_role_status(user_id: str,
                            req: UserRoleStatusUpdate,
                            admin: dict = Depends(require_admin),
                            store: Store = Depends(get_store)) -> dict:
    """管理员审核/调整用户角色与认证状态。"""
    target = store.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    new_role = req.role if req.role is not None else target["role"]
    if new_role not in BUSINESS_ROLES:
        raise HTTPException(status_code=422, detail="当前仅开放教师、学生和系统管理员")
    new_status = req.status if req.status in ("active", "pending", "rejected") else target.get("status", "active")

    # 防止取消唯一内置管理员自身角色
    if target["username"] == "admin" and (new_role != "admin" or new_status != "active"):
        raise HTTPException(status_code=400, detail="不可降级或禁用系统内置管理员")

    store.update_user_role_and_status(user_id, new_role, new_status)
    if new_status != "active":
        store.revoke_user_sessions(user_id, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    updated = store.get_user(user_id)
    return public_user(updated)


@router.post("/admin/users/{user_id}/role-certification")
def decide_role_certification(user_id: str, req: RoleCertificationDecision,
                              admin: dict = Depends(require_admin),
                              store: Store = Depends(get_store)) -> dict:
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.decision not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="decision 只能是 approve 或 reject")
    if req.decision == "approve":
        if target.get("requested_role") not in {"student", "teacher"}:
            raise HTTPException(status_code=409, detail="用户没有待审核的学校角色申请")
        target["role"] = target["requested_role"]
        target["status"] = "active"
        target["organization_context"] = target.get("requested_organization_context") or {}
        target["role_application_status"] = "approved"
    else:
        target["role_application_status"] = "rejected"
    target["requested_role"] = None
    target["requested_organization_context"] = {}
    _save_user_metadata(store, target, (
        "role", "status", "organization_context", "requested_role",
        "requested_organization_context", "role_application_status",
    ), actor=admin)
    return public_user(store.get_user(user_id))
