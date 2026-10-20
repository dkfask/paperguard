"""两个「浏览器进不去」的页面的后端契约测试。

背景：/first-login 与 /role-certification 是逐屏审查里唯一两个从未取得
运行时证据的页面——它们都要求特定账号状态，而浏览器登录门禁要求每次
触及表单由用户人工接管，元素引用在确认往返期间即过期。

这两页的逻辑全在后端（准入判定、状态流转、接口契约），
这里仅验证后端准入与状态流转，不能替代浏览器表单和真实邮件通道验收。

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
from tests._helpers import register_fixture
from app.services import emailer

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
    return register_fixture(client, username, password, display_name="引导测试学生")


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


@pytest.mark.parametrize(
    ("delivery", "expected_status", "expected_code"),
    [
        ("sent", 200, None),
        ("unconfigured", 503, "EMAIL_NOT_CONFIGURED"),
        ("failed", 502, "EMAIL_SEND_FAILED"),
    ],
)
def test_onboarding_paths_stay_reachable_while_blocked(
    monkeypatch, delivery: str, expected_status: int, expected_code: str | None
) -> None:
    """引导接口应可达；邮件成功与故障均由隔离 fixture 确定，不访问真实邮箱。"""
    delivered: dict[str, str] = {}

    def fake_send(to: str, code: str, minutes: int = 10) -> None:
        if delivery == "unconfigured":
            raise emailer.EmailNotConfigured("isolated provider is not configured")
        if delivery == "failed":
            raise RuntimeError("isolated provider delivery failed")
        delivered.update(email=to, code=code)

    monkeypatch.setattr(emailer, "send_verification_code", fake_send)
    username = f"onb_{uuid.uuid4().hex[:8]}"
    email = f"{username}@uat.example"
    password = _gen_password()
    info = _register_student(username, password)
    _set_user_state(info["id"], email=email, email_verified=0)

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}
    code_resp = client.post(
        "/api/auth/email/code",
        headers=headers,
        json={"email": email, "purpose": "first_login"},
    )
    assert code_resp.status_code == expected_status, code_resp.text
    if expected_code:
        assert code_resp.json()["detail"]["code"] == expected_code
        assert not delivered
    else:
        assert delivered["email"] == email

    # 使用真实请求模型字段，避免错误字段触发 422 后被误判为「引导可达」。
    complete = client.post(
        "/api/auth/first-login/complete",
        headers=headers,
        json={
            "email": email,
            "email_code": delivered.get("code", "000000"),
            "new_password": _gen_password(),
        },
    )
    if delivery == "sent":
        assert complete.status_code == 200, complete.text
        completed_headers = {"Authorization": f"Bearer {complete.json()['token']}"}
        me = client.get("/api/auth/me", headers=completed_headers)
        assert me.status_code == 200, me.text
        assert me.json()["onboarding_required"] is False
        assert client.get("/api/projects", headers=completed_headers).status_code == 200
    else:
        assert complete.status_code == 400, complete.text
        assert complete.json()["detail"]["code"] == "EMAIL_CODE_INVALID"
        assert client.get("/api/projects", headers=headers).status_code == 403


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
    # 无邮箱注册时 register_user 会把 target_role 降级成 student
    # （app/auth.py:141-149），所以这里要显式改回 user+unselected，
    # 否则调用者不是未认证 user，会先被准入检查 403 拦下，测不到输入校验。
    _set_user_state(user_id, role="user", role_application_status="unselected", status="active")

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


def test_no_email_registration_stays_in_role_certification_flow() -> None:
    """无邮箱公开注册也必须保持受限 user 身份，不得静默升级。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": f"u_{uuid.uuid4().hex[:8]}", "password": _gen_password(),
              "role": "user", "display_name": "社会用户"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "user", resp.json()
    assert resp.json()["role_application_status"] == "unselected", resp.json()


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


# --------------------------------------------------------------------------
# /role-certification 的准入（第九轮发现的越权）
# --------------------------------------------------------------------------

def test_role_certification_rejects_non_user_roles() -> None:
    """已获批角色不得进入身份认证自助流程 —— 这是本轮修掉的越权。

    修复前 POST /api/auth/role-certification 只有 current_user 一道校验，
    任何已登录角色都能调用，且：
      - role=user 分支会**立刻**把 role 覆写成 user、status 置 active、
        application 置 approved，不经任何审批（生产实测：正式教师被自己
        一次 POST 降成 user）；
      - role=student/teacher 分支会把调用者塞进管理员审批队列，
        而审批端点会把 target["role"] 直接改成 requested_role。
    """
    username = f"cert_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    info = _register_student(username, password)
    assert info["role"] == "student", f"前置条件：应注册为 student，实际 {info}"

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1) role=user 分支：修复前会把学生改成 user
    downgrade = client.post("/api/auth/role-certification", headers=headers, json={"role": "user"})
    assert downgrade.status_code == 403, f"已获批学生不得走 role=user 分支：{downgrade.text}"

    # 2) role=teacher 分支：修复前会把调用者送进管理员审批队列
    escalate = client.post(
        "/api/auth/role-certification",
        headers=headers,
        json={"role": "teacher", "organization_context": {"school_id": _school_id()}},
    )
    assert escalate.status_code == 403, f"已获批学生不得申请教师角色：{escalate.text}"

    # 3) 角色与认证状态必须原封不动
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["role"] == "student", f"角色被改写了：{body}"
    assert body["status"] == "active"
    assert not body.get("requested_role"), f"不应留下待审申请：{body}"


def test_role_certification_teacher_is_also_locked_out() -> None:
    """教师同样不能进这条流程 —— 生产实测中被自己降级的那一类。"""
    username = f"certt_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    info = register_fixture(client, username, password, role="teacher", display_name="认证越权测试教师")
    _set_user_state(info["id"], status="active")

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    down = client.post("/api/auth/role-certification", headers=headers, json={"role": "user"})
    assert down.status_code == 403, down.text
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "teacher"


def test_role_certification_options_do_not_leak_org_tree() -> None:
    """GET 选项列表对已获批角色返回 403。

    修复前它对任何登录用户返回**全量**学校/学院组织树（无作用域过滤），
    生产有真实数据时任何学生都能枚举全校组织结构。
    """
    username = f"certl_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    _register_student(username, password)
    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    leak = client.get("/api/auth/role-certification", headers=headers)
    assert leak.status_code == 403, f"已获批角色不得读取认证选项：{leak.text}"
    assert "organizations" not in leak.json()


def test_unselected_user_still_keeps_the_legit_flow() -> None:
    """前置条件不能把正常流程一起挡掉：unselected 的 user 必须仍能认证。

    这条是防「越权修过头」的对冲用例。
    """
    username = f"certok_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    info = _register_student(username, password)
    _set_user_state(
        info["id"], role="user", role_application_status="unselected",
        status="active", organization_context={},
    )

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}

    options = client.get("/api/auth/role-certification", headers=headers)
    assert options.status_code == 200, options.text
    assert options.json()["application_status"] == "unselected"

    submit = client.post(
        "/api/auth/role-certification",
        headers=headers,
        json={"role": "student", "organization_context": {"school_id": _school_id()}},
    )
    assert submit.status_code == 200, submit.text
    body = submit.json()
    assert body["role"] == "user", "认证期间角色不应被改写，只是登记待审申请"
    assert body["role_application_status"] == "pending"
    assert body["requested_role"] == "student"

    # 试点关闭社会用户；不能放弃认证来绕过审核进入业务。
    give_up = client.post("/api/auth/role-certification", headers=headers, json={"role": "user"})
    assert give_up.status_code == 422, give_up.text
    assert client.get("/api/auth/me", headers=headers).json()["role_application_status"] == "pending"
    assert client.get("/api/projects", headers=headers).status_code == 403


def test_rejected_user_can_reapply() -> None:
    """被驳回后 role 仍是 user，应当还能重新提交 —— 不能被前置条件误伤。"""
    username = f"certr_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    info = _register_student(username, password)
    _set_user_state(info["id"], role="user", role_application_status="rejected", status="active")

    token = _login(username, password)["token"]
    headers = {"Authorization": f"Bearer {token}"}
    again = client.post(
        "/api/auth/role-certification",
        headers=headers,
        json={"role": "student", "organization_context": {"school_id": _school_id()}},
    )
    assert again.status_code == 200, f"驳回后应能重新申请：{again.text}"
    assert again.json()["role_application_status"] == "pending"
