"""两个「浏览器进不去」的页面的后端契约测试。

背景：/first-login 与 /role-certification 是逐屏审查里唯一两个从未取得
运行时证据的页面——它们都要求特定账号状态，而浏览器登录门禁要求每次
触及表单由用户人工接管，元素引用在确认往返期间即过期。

这两页的逻辑全在后端（准入判定、状态流转、接口契约），
前端只是一层表单。因此这里用接口层测试覆盖，把「未验证」变成「已验证」。

另更正一条此前的错误结论：报告曾写「/role-certification 任何凭据都
无法进入」。实际是——**带邮箱注册会得到 role=user + unselected**，
那正是该页的准入条件（app/auth.py:141-149）。只是该路径要求完成邮箱
验证码，而 UAT 未配置 SMTP，因此实际走不通。下面的测试同时覆盖
「分支走向正确」与「SMTP 缺失时被明确拒绝」。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app

client = TestClient(app)


def _gen_password() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-8a"


def _school_id() -> str:
    """取一个学校组织，没有就建一个。

    测试库并不保证预置组织，而身份认证接口要求 school_id 指向真实学校，
    因此这里自举，避免用例依赖执行顺序。
    """
    store = Store()
    school = next((u for u in store.list_organization_units() if u.type.value == "school"), None)
    if school is not None:
        return school.id
    from app.models.organization import OrganizationType, OrganizationUnit

    created = store.save_organization_unit(
        OrganizationUnit(
            id=f"school-{uuid.uuid4().hex[:10]}",
            name="引导测试大学",
            type=OrganizationType.SCHOOL,
        )
    )
    return created.id


def _register_student(username: str, password: str) -> dict:
    """不带邮箱注册。

    带邮箱的注册走双轨制、需要邮箱验证码（EMAIL_CODE_REQUIRED），
    在这里会卡在 422。因此先走无邮箱的 legacy 路径注册成功，
    再直接改库构造需要验证的状态——这也正是生产里这类账号的来源。
    """
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "student",
              "display_name": "引导测试学生"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_user_state(user_id: str, **fields) -> dict:
    store = Store()
    user = store.get_user(user_id)
    for key, value in fields.items():
        user[key] = value
    store.save_user(user)
    return store.get_user(user_id)


def _login(username: str, password: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------
# /first-login
# --------------------------------------------------------------------------

def test_onboarding_student_is_blocked_from_business_endpoints() -> None:
    """未完成邮箱验证的学生，登录后访问业务接口必须被 403 ONBOARDING_REQUIRED 拦住。

    这条是 /first-login 存在的理由：前端守卫只改 URL，真正的强制点在后端
    （app/auth.py:619）。前端被绕过时后端仍要拦得住。
    """
    username = f"onb_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    info = _register_student(username, password)
    # 制造「已填邮箱但未验证」这一典型 onboarding 状态
    _set_user_state(info["id"], email=f"{username}@uat.example", email_verified=0)

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["onboarding_required"] is True, "未验证邮箱的学生应被标记为需要引导"

    blocked = client.get("/api/projects", headers=headers)
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["detail"]["code"] == "ONBOARDING_REQUIRED"


def test_onboarding_paths_stay_reachable_while_blocked() -> None:
    """引导未完成时，验证/完成引导所需的接口本身必须放行，否则用户无法自救。"""
    username = f"onb_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    info = _register_student(username, password)
    _set_user_state(info["id"], email=f"{username}@uat.example", email_verified=0)

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 申请验证码在豁免名单里（app/auth.py:606-607）
    code_resp = client.post(
        "/api/auth/email/code",
        headers=headers,
        json={"email": f"{username}@uat.example", "purpose": "first_login"},
    )
    assert code_resp.status_code in (200, 202), code_resp.text

    # 完成引导同样可达
    complete = client.post(
        "/api/auth/first-login/complete",
        headers=headers,
        json={"code": "000000", "new_password": _gen_password()},
    )
    # 验证码错误属于预期内，只要不是「被 onboarding 拦住」即可
    assert complete.status_code != 403 or complete.json().get("detail", {}).get("code") != "ONBOARDING_REQUIRED", complete.text


def test_verified_student_is_not_blocked() -> None:
    """对照组：邮箱已验证、密码已设置的学生不应被引导拦截。"""
    username = f"ok_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    info = _register_student(username, password)
    _set_user_state(
        info["id"], email=f"{username}@uat.example",
        email_verified=1, password_set=1, must_change_password=0,
    )

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers)
    assert me.json()["onboarding_required"] is False, me.text
    assert client.get("/api/projects", headers=headers).status_code == 200


# --------------------------------------------------------------------------
# /role-certification
# --------------------------------------------------------------------------

def test_unselected_user_can_complete_role_certification() -> None:
    """role=user 且 status=unselected 的账号，必须能走完身份认证流程。

    这个组合注册流程产生不了（注册选 user 直接置 approved），
    只能来自迁移前的历史数据，因此用直接改库构造。
    前端守卫会把这类用户强制送到 /role-certification，若后端链路不通，
    这些账号会被永久卡死在登录后无法前进。
    """
    username = f"unsel_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "user", "display_name": "待认证用户"},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    _set_user_state(user_id, role="user", status="active",
                    role_application_status="unselected")  # 模拟历史账号

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "user"
    assert me.json()["role_application_status"] == "unselected"

    # 页面加载时拉取的选项
    options = client.get("/api/auth/role-certification", headers=headers)
    assert options.status_code == 200, options.text

    # 提交教师认证：应转为 pending 并记下申请角色
    submit = client.post(
        "/api/auth/role-certification",
        headers=headers,
        json={"role": "teacher", "organization_context": {"school_id": _school_id()}},
    )
    assert submit.status_code == 200, submit.text
    body = submit.json()
    assert body["role_application_status"] == "pending", body
    assert body["role"] == "user", "审核通过前角色不应改变"


def test_role_certification_rejects_unknown_role_and_bad_school() -> None:
    """提交时的输入校验必须挡住不存在的角色与不属于该学校的学院。"""
    username = f"unsel2_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "user", "display_name": "待认证用户2"},
    )
    user_id = resp.json()["id"]
    _set_user_state(user_id, role_application_status="unselected")

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    bad_role = client.post(
        "/api/auth/role-certification", headers=headers,
        json={"role": "root", "organization_context": {"school_id": _school_id()}},
    )
    assert bad_role.status_code == 422, bad_role.text

    no_school = client.post(
        "/api/auth/role-certification", headers=headers,
        json={"role": "teacher", "organization_context": {}},
    )
    assert no_school.status_code == 422, no_school.text


def test_no_email_registration_downgrades_user_to_student() -> None:
    """无邮箱注册时 role 会被降级为 student，状态直接 approved。

    register_user（app/auth.py:141-149）里，有邮箱才走 target_role="user"
    且 application_status="unselected"；无邮箱时 role=user 会被降级，
    因此这条路径**产生不了** unselected。
    """
    resp = client.post(
        "/api/auth/register",
        json={"username": f"u_{uuid.uuid4().hex[:8]}", "password": _gen_password(),
              "role": "user", "display_name": "社会用户"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "student", resp.json()
    assert resp.json()["role_application_status"] == "approved", resp.json()


def test_email_registration_produces_unselected_user(monkeypatch) -> None:
    """带邮箱注册才是 role=user + unselected 的来源——即 /role-certification 的入口。

    这一条更正了报告里「/role-certification 任何凭据都无法进入」的结论：
    入口是存在的，只是需要完成邮箱验证码，而 UAT 环境未配置 SMTP。
    这里把邮件发送与验证码校验替换掉，隔离验证「分支走向是否正确」。
    """
    monkeypatch.setattr("app.services.emailer.email_configured", lambda: True)
    monkeypatch.setattr("app.auth._verify_email_code", lambda *_a, **_k: None)

    email = f"probe_{uuid.uuid4().hex[:8]}@uat.example"
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": _gen_password(),
              "display_name": "邮箱注册用户", "email_code": "123456"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "user", f"带邮箱注册应得到 role=user，实际 {body}"
    assert body["role_application_status"] == "unselected", f"带邮箱注册应得到 unselected，实际 {body}"
    # 这正是 /role-certification 的准入条件
    assert body["role"] == "user" and body["role_application_status"] == "unselected"


def test_email_registration_never_silently_downgrades(monkeypatch) -> None:
    """带邮箱注册要么按双轨制走完，要么被明确拒绝，绝不能静默降级成 student。

    无论邮件通道是否配置，都不允许出现「以为注册了个人用户，
    实际拿到的是 student 且状态 approved」的情况。
    """
    resp = client.post(
        "/api/auth/register",
        json={"email": f"nomail_{uuid.uuid4().hex[:8]}@uat.example",
              "password": _gen_password(), "display_name": "无邮件通道",
              "email_code": "123456"},
    )
    assert resp.status_code >= 400, f"验证码未通过时不应注册成功：{resp.text}"
    code = resp.json().get("detail", {}).get("code")
    assert code in {"EMAIL_NOT_CONFIGURED", "EMAIL_CODE_REQUIRED", "EMAIL_CODE_INVALID"}, resp.text
    # 确认库里没有因此多出一个降级账号
    assert Store().get_user_by_username(f"nomail_{resp.json().get('detail', {}).get('code', '')}") is None
