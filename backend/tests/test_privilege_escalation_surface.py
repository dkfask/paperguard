"""「谁能改自己的角色/状态/作用域」这一类缺陷的结构性护栏。

背景：P0-01（第九轮）——`POST /api/auth/role-certification` 只有
`Depends(current_user)`，没有「调用方必须是尚未选定学校角色的 user」
这道前置条件，于是任何已登录角色都能覆写自己的 role/status，
一个正式教师被自己一次 POST 降成 user，全程无管理员参与。

为什么这个缺陷前几轮没被审计抓到：此前的写操作审计覆盖的是
**按路径参数枚举**（拿别人的 id 去改），而「改**自己**的记录」走的是
`Depends(current_user)` 拿到的对象，没有路径参数，天然落在枚举之外。
这是一个方法盲区，不是一次执行疏忽。

本文件把这个盲区变成结构性护栏：用 AST 扫出所有**对 current_user 派生对象
直接写入敏感字段**的位置，逐个要求它必须落在显式允许名单里。
以后有人新增这类写入而忘了加前置条件，测试会直接失败并点名，
而不是等下一次安全扫描才发现。
"""

from __future__ import annotations

import ast
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "app" / "api"

# 改写权限相关字段的敏感字段名
SENSITIVE_FIELDS = {
    "role",
    "status",
    "organization_context",
    "role_application_status",
    "requested_role",
    "must_change_password",
}

# 允许写入敏感字段的函数，以及它凭什么安全。
#
# 这里必须「白名单」而不是「黑名单」：白名单要求每处写入都显式说明理由，
# 任何新增的写入默认是**不允许**的，会直接把测试打红。
ALLOWED_WRITERS = {
    # 第九轮修复后带了前置条件：只有 role == "user" 的未认证账号能进来
    ("auth.py", "submit_role_certification"): "有 _require_unselected_user 前置条件",
    ("auth.py", "decide_role_certification"): "管理员审批端点，require_admin",
    ("auth.py", "update_user_role_and_status_endpoint"): "管理员端点，require_admin",
    # 改的是**别人**的组织作用域，不是改自己的；且有三道校验：
    # require_manager + 显式角色白名单(admin/college_admin) + 学院管理员
    # 只能设自己可见范围内的组织子集。
    ("organizations.py", "update_user_context"): "require_manager + 角色白名单 + 作用域子集校验",
}


def _enclosing_function(node: ast.AST, parents: list[ast.AST]) -> str:
    for parent in reversed(parents):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
    return "<module-level>"


def _sensitive_writes(path: Path) -> list[tuple[str, int, str]]:
    """返回 (函数名, 行号, 字段) 三元组：对该文件里形如 user/target 的
    局部变量名直接写入敏感字段的位置。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents: list[ast.AST] = []
    hits: list[tuple[str, int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit(self, node: ast.AST) -> None:
            parents.append(node)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    # 只看 x["field"] = ... 这种下标赋值
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in {"user", "target"}
                    ):
                        key = target.slice
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            if key.value in SENSITIVE_FIELDS:
                                func = _enclosing_function(node, parents)
                                hits.append((func, node.lineno, key.value))
            super().visit(node)
            parents.pop()

    Visitor().visit(tree)
    return hits


def test_no_unauthorized_self_escalation_writes() -> None:
    """对 current_user 派生对象的敏感字段写入，必须全部落在允许名单里。"""
    unauthorized: list[str] = []
    audited = 0

    for path in sorted(API_DIR.glob("*.py")):
        for func, lineno, field in _sensitive_writes(path):
            audited += 1
            key = (path.name, func)
            if key not in ALLOWED_WRITERS:
                unauthorized.append(
                    f"{path.name}:{lineno} {func}() 直接写入 {field}"
                )

    assert audited > 0, (
        "扫描结果为 0，说明 AST 匹配规则失效而不是真的没有写入点——"
        "护栏本身失效比没有护栏更危险"
    )
    assert not unauthorized, (
        "以下位置对 current_user 派生对象直接写了权限敏感字段，"
        "但不在显式允许名单里。若确属安全，请把它连同「凭什么安全」"
        "一起加进 ALLOWED_WRITERS，强制下一个改代码的人看见：\n  "
        + "\n  ".join(unauthorized)
    )


def test_role_certification_keeps_its_precondition() -> None:
    """P0-01 的修复不能被悄悄撤掉。"""
    source = (API_DIR / "auth.py").read_text(encoding="utf-8")
    assert "_require_unselected_user" in source, "前置条件函数不见了"
    # 两个端点各自都要调用
    # 注意：函数定义写作 `def _require_unselected_user(user: dict)`，
    # 不含下面这个子串，所以这里数的是**两处调用**（GET + POST）
    assert source.count("_require_unselected_user(user)") >= 2, (
        "预期 GET 与 POST 两个端点各调用一次，"
        f"实际 {source.count('_require_unselected_user(user)')} 处"
    )
    guard = (
        'if user.get("role") != "user":'
    )
    assert guard in source, "前置条件的判据被改了——必须是「角色不是 user 就拒绝」"
