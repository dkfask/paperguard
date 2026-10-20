"""Single-school pilot role admission, shared by both authentication boundaries."""

from fastapi import HTTPException


BUSINESS_ROLES = frozenset({"admin", "teacher", "student"})
_CERTIFICATION_PATHS = frozenset({
    "/api/auth/me", "/api/auth/logout", "/api/auth/change-password",
    "/api/auth/role-certification",
})


def require_session_role(user: dict, *, platform_bound: bool = False) -> None:
    # `user` is an onboarding identity only; it never grants business access.
    # Explicitly approved platform operators may retain a legacy global role.
    # The binding is checked at the authentication boundary; it never grants
    # ordinary business access to that legacy role.
    if user.get("role") not in BUSINESS_ROLES | {"user"} and not platform_bound:
        raise HTTPException(status_code=403, detail={
            "code": "ROLE_NOT_OPEN",
            "message": "当前仅开放教师、学生和系统管理员，请联系系统管理员调整身份",
        })


def require_business_role(user: dict, *, path: str = "") -> None:
    require_session_role(user)
    if user.get("role") == "user" and path not in _CERTIFICATION_PATHS:
        raise HTTPException(status_code=403, detail={
            "code": "ROLE_CERTIFICATION_REQUIRED",
            "message": "请申请教师或学生身份，审核通过后即可使用",
        })
