"""Explicit server-side approvals for the limited shared-SaaS pilot.

No automatic platform mapping, account merging, registration membership or
activation. Offline operators alone may record a platform approval. HTTP callers
must use the resulting current binding, never a global role string.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.context import RequestContext
from app.db import Store, _now, new_id


def fail(status, code, message, *, checks=None):
    detail = {"code":code,"message":message}
    if checks is not None:
        detail["checks"] = checks
    raise HTTPException(status_code=status, detail=detail)


def platform_binding(store: Store, user_id: str):
    with store._connect() as conn:
        row=conn.execute("SELECT * FROM platform_role_bindings WHERE user_id=? AND role_code='platform_admin' AND status='active'",(user_id,)).fetchone()
    return dict(row) if row else None


def approve_platform_role(store: Store, user_id: str, *, approval_ref: str, approved_by: str):
    """Offline operator entry only; NOT called from seed, login, import or HTTP.

    An explicit reviewed approval reference is required. This does not declare
    Q-09 globally decided or grant any tenant membership.
    """
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}",approval_ref) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}",approved_by):
        raise ValueError("explicit non-PII approval references required")
    with store.workflow_transaction() as conn:
        user=conn.execute("SELECT status,must_change_password FROM users WHERE id=?",(user_id,)).fetchone()
        if not user or user['status']!='active' or user['must_change_password']:raise ValueError("active account required")
        row=conn.execute("SELECT * FROM platform_role_bindings WHERE user_id=?",(user_id,)).fetchone()
        if row and row['status']=='active' and row['approval_ref']==approval_ref and row['approved_by']==approved_by:return dict(row)
        if row:
            conn.execute("UPDATE platform_role_bindings SET status='active',auth_version=auth_version+1,approval_ref=?,approved_by=? WHERE user_id=?",(approval_ref,approved_by,user_id))
        else:
            conn.execute("INSERT INTO platform_role_bindings VALUES (?,?,?,'active',1,?,?,?)",(new_id('platform-binding'),user_id,'platform_admin',approval_ref,approved_by,_now()))
        store.save_audit_event_conn(conn,{'actor_id':approved_by,'action':'platform.explicit_approval','resource_type':'platform_role_binding','resource_id':user_id,'reason':json.dumps({'approval_ref':approval_ref})})
        return dict(conn.execute("SELECT * FROM platform_role_bindings WHERE user_id=?",(user_id,)).fetchone())


def check_platform(conn, ctx: RequestContext):
    if ctx.actor_kind!='platform' or not ctx.has_capability('operations:manage'):
        fail(403,'CAPABILITY_REQUIRED','需要显式批准的平台身份')
    row=conn.execute("SELECT b.id FROM platform_role_bindings b JOIN users u ON u.id=b.user_id WHERE b.user_id=? AND b.status='active' AND b.auth_version=? AND u.status='active' AND u.must_change_password=0",(ctx.actor_id,ctx.auth_version)).fetchone()
    if not row:fail(403,'AUTHORIZATION_STALE','平台授权已撤销或版本已变化')


def check_member(conn, ctx: RequestContext, capability: str):
    if ctx.actor_kind!='member' or not ctx.has_capability(capability):fail(403,'CAPABILITY_REQUIRED','当前成员无权执行此操作')
    row=conn.execute("SELECT m.capabilities FROM tenant_memberships m JOIN users u ON u.id=m.user_id JOIN tenants t ON t.id=m.tenant_id WHERE m.id=? AND m.user_id=? AND m.tenant_id=? AND m.auth_version=? AND m.status='active' AND u.status='active' AND u.must_change_password=0 AND t.status='active'",(ctx.membership_id,ctx.actor_id,ctx.tenant_id,ctx.auth_version)).fetchone()
    if not row or capability not in json.loads(row['capabilities']):fail(403,'AUTHORIZATION_STALE','成员授权或租户状态已变化')


def audit(store,conn,ctx,action,resource_id,**metadata):
    store.save_audit_event_conn(conn,{'actor_id':ctx.actor_id,'action':action,'resource_type':'saas','resource_id':resource_id,'request_id':ctx.request_id,'reason':json.dumps({'tenant_id':ctx.tenant_id,'membership_id':ctx.membership_id,'auth_version':ctx.auth_version,**metadata},sort_keys=True)})


def create_draft(store,ctx,request,key):
    if not key or not re.fullmatch(r'[A-Za-z0-9._:-]{1,120}',key):fail(422,'IDEMPOTENCY_KEY_REQUIRED','需要有效幂等键')
    payload=request.model_dump(mode='json');fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    with store.workflow_transaction() as conn:
        check_platform(conn,ctx)
        previous=conn.execute('SELECT * FROM saas_provisioning WHERE command_key=?',(key,)).fetchone()
        if previous:
            if previous['actor_id']!=ctx.actor_id or previous['fingerprint']!=fingerprint:fail(409,'IDEMPOTENCY_CONFLICT','幂等键对应另一建户命令')
            return dict(conn.execute('SELECT * FROM tenants WHERE id=?',(previous['tenant_id'],)).fetchone()),True
        if conn.execute('SELECT id FROM tenants WHERE slug=?',(request.slug,)).fetchone():fail(409,'TENANT_SLUG_TAKEN','租户标识已存在')
        approval=request.approval
        for uid in {approval.responsible_user_id,approval.exit_contact_user_id}:
            if not conn.execute("SELECT id FROM users WHERE id=? AND status='active'",(uid,)).fetchone():fail(409,'TENANT_APPROVAL_INVALID','负责人或退出联系人不是活动账号')
        org=conn.execute('SELECT data FROM organization_units WHERE id=?',(approval.organization_root_id,)).fetchone()
        body=json.loads(org['data']) if org else {}
        if body.get('type')!='school' or body.get('parent_id') or body.get('status','active')!='active':fail(409,'TENANT_APPROVAL_INVALID','组织必须是经服务端核对的有效学校根')
        if approval.pilot_expires_at <= datetime.now(timezone.utc):fail(409,'TENANT_APPROVAL_INVALID','试点期限已失效')
        tid=new_id('tenant');now=_now()
        conn.execute("INSERT INTO tenants VALUES (?,?,?,'draft',1,?,?)",(tid,request.slug,request.name,now,now))
        conn.execute('INSERT INTO saas_provisioning (command_key,tenant_id,actor_id,fingerprint,approval,created_at) VALUES (?,?,?,?,?,?)',(key,tid,ctx.actor_id,fingerprint,json.dumps(payload['approval'],sort_keys=True),now))
        audit(store,conn,ctx,'tenant.create_draft',tid,approval_ref=approval.approval_ref)
        return dict(conn.execute('SELECT * FROM tenants WHERE id=?',(tid,)).fetchone()),False


def add_member(store,ctx,tenant_id,request):
    """Create or exact replay, not an authorization-upsert/reactivation path."""
    with store.workflow_transaction() as conn:
        check_platform(conn,ctx)
        if not conn.execute("SELECT id FROM tenants WHERE id=? AND status='active'",(tenant_id,)).fetchone():fail(404,'TENANT_NOT_FOUND','租户不存在或未启用')
        target = conn.execute("SELECT status,must_change_password FROM users WHERE id=?",(request.user_id,)).fetchone()
        if not target:fail(404,'USER_NOT_FOUND','目标账号不存在')
        if target['status']!='active' or target['must_change_password']:fail(409,'MEMBERSHIP_ACCOUNT_INELIGIBLE','目标账号不可建成员')
        existing=conn.execute('SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?',(tenant_id,request.user_id)).fetchone()
        caps=sorted(request.capabilities)
        if existing:
            if existing['status']!='active':fail(409,'MEMBERSHIP_REACTIVATION_FORBIDDEN','已撤权成员不得由重复建号复活')
            if existing['role']!=request.role or json.loads(existing['capabilities'])!=caps:fail(409,'MEMBERSHIP_ALREADY_EXISTS','授权变更必须走独立授权协议')
            return Store._membership_row(existing),True
        mid=new_id('membership');now=_now()
        conn.execute("INSERT INTO tenant_memberships VALUES (?,?,?,?,?,'active',1,?,?)",(mid,tenant_id,request.user_id,request.role,json.dumps(caps),now,now))
        audit(store,conn,ctx,'membership.create',mid,target_tenant_id=tenant_id)
        return Store._membership_row(conn.execute('SELECT * FROM tenant_memberships WHERE id=?',(mid,)).fetchone()),False


def assign_first_owner(store,ctx,tenant_id,request):
    from app.services.tenant_access import ROLE_CAPABILITIES
    with store.workflow_transaction() as conn:
        check_platform(conn,ctx)
        t=conn.execute('SELECT * FROM tenants WHERE id=?',(tenant_id,)).fetchone()
        p=conn.execute('SELECT * FROM saas_provisioning WHERE tenant_id=?',(tenant_id,)).fetchone()
        if not t or t['status']!='draft' or not p:fail(409,'TENANT_OWNER_SETUP_FORBIDDEN','仅显式批准的draft客户可设置首位所有者')
        user=conn.execute('SELECT * FROM users WHERE id=?',(request.user_id,)).fetchone()
        if not user or user['status']!='active' or not user['email_verified'] or not user['password_set'] or user['must_change_password']:fail(409,'OWNER_IDENTITY_UNVERIFIED','首位所有者须为已验证并完成设置的活动账号')
        existing=conn.execute('SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?',(tenant_id,request.user_id)).fetchone()
        if p['first_owner_id']:
            if p['first_owner_id']==request.user_id and p['owner_request_version']==request.expected_version and existing and existing['status']=='active' and existing['auth_version']==p['owner_auth_version']:
                return Store._membership_row(existing),True
            fail(409,'TENANT_OWNER_SETUP_FORBIDDEN','首位所有者已设置或授权已变化')
        if t['auth_version']!=request.expected_version:fail(409,'TENANT_VERSION_CONFLICT','租户版本已变化')
        if existing:fail(409,'TENANT_OWNER_SETUP_FORBIDDEN','已有成员不得由兼容路径隐式升为owner')
        mid=new_id('membership');now=_now();caps=json.dumps(sorted(ROLE_CAPABILITIES['admin']))
        conn.execute("INSERT INTO tenant_memberships VALUES (?,?,?,'admin',?,'active',1,?,?)",(mid,tenant_id,request.user_id,caps,now,now))
        conn.execute('UPDATE saas_provisioning SET first_owner_id=?,owner_request_version=?,owner_auth_version=1 WHERE tenant_id=?',(request.user_id,request.expected_version,tenant_id))
        conn.execute('UPDATE tenants SET auth_version=auth_version+1,updated_at=? WHERE id=?',(now,tenant_id))
        audit(store,conn,ctx,'tenant.first_owner',tenant_id,target_user_id=request.user_id)
        return Store._membership_row(conn.execute('SELECT * FROM tenant_memberships WHERE id=?',(mid,)).fetchone()),False


def refuse_activation(store,ctx,tenant_id,expected_version):
    """Explicit entry, fail closed until the complete CL-6 readiness facts exist."""
    with store.workflow_transaction() as conn:
        check_platform(conn,ctx)
        t=conn.execute('SELECT * FROM tenants WHERE id=?',(tenant_id,)).fetchone()
        if not t or t['status']!='draft':fail(409,'TENANT_ACTIVATION_BLOCKED','租户不在可启用的draft状态')
        if t['auth_version']!=expected_version:fail(409,'TENANT_VERSION_CONFLICT','租户版本已变化')
        # No client-supplied readiness booleans are accepted as facts. Owner
        # assignment cannot stand in for template/entitlement/AI/org readiness.
        owner = conn.execute("SELECT u.id FROM saas_provisioning p JOIN tenant_memberships m ON m.tenant_id=p.tenant_id AND m.user_id=p.first_owner_id JOIN users u ON u.id=m.user_id WHERE p.tenant_id=? AND m.status='active' AND m.auth_version=p.owner_auth_version AND u.status='active' AND u.email_verified=1 AND u.password_set=1 AND u.must_change_password=0", (tenant_id,)).fetchone()
        checks = {name: "unverified" for name in ("organization", "published_template", "member_admission", "entitlements_quotas", "ai_policy", "readiness")}
        checks["owner"] = "passed" if owner else "blocked"
        fail(409,'TENANT_ACTIVATION_BLOCKED','客户启用缺少服务端readiness证明；禁止启用',checks=checks)
