"""Tenant/member-scoped writing pilot. No shared template, file or job fallback."""
from __future__ import annotations

import json

from app.db import _now, new_id
from app.models.project import WritingProject
from app.services.saas_identity import audit, check_member, fail


def _scope_sql():
    return "json_extract(data,'$.tenant_id')=? AND json_extract(data,'$.membership_id')=? AND json_extract(data,'$.owner_id')=?"


def _params(ctx):
    return (ctx.tenant_id,ctx.membership_id,ctx.actor_id)


def _get(conn,ctx,project_id):
    row=conn.execute('SELECT data FROM projects WHERE id=? AND '+_scope_sql(),(project_id,*_params(ctx))).fetchone()
    project=WritingProject.model_validate_json(row['data']) if row else None
    if not project or project.deleted_at:fail(404,'PROJECT_SCOPE_NOT_FOUND','项目不存在或不可见')
    return project


def list_projects(store,ctx):
    with store.workflow_transaction() as conn:
        check_member(conn,ctx,'project:read')
        rows=conn.execute('SELECT data FROM projects WHERE '+_scope_sql()+" AND coalesce(json_extract(data,'$.deleted_at'),'')='' ORDER BY updated_at DESC,id",_params(ctx)).fetchall()
        # List is metadata-only, never manuscript sections or names of other owners.
        return [{'id':p.id,'title':p.title,'tenant_id':p.tenant_id,'membership_id':p.membership_id,'revision':p.revision,'updated_at':p.updated_at} for p in (WritingProject.model_validate_json(r['data']) for r in rows)]


def get_project(store,ctx,project_id):
    with store.workflow_transaction() as conn:
        check_member(conn,ctx,'project:read')
        return _get(conn,ctx,project_id)


def create_project(store,ctx,request):
    with store.workflow_transaction() as conn:
        check_member(conn,ctx,'project:write')
        now=_now();p=WritingProject(id=new_id('project'),template_id='',title=request.title,sections=request.sections,
            owner_id=ctx.actor_id,tenant_id=ctx.tenant_id,membership_id=ctx.membership_id,revision=1,created_at=now,updated_at=now)
        conn.execute('INSERT INTO projects (id,template_id,data,updated_at) VALUES (?,?,?,?)',(p.id,p.template_id,p.model_dump_json(),now))
        audit(store,conn,ctx,'project.create',p.id)
        return p


def update_project(store,ctx,project_id,request):
    with store.workflow_transaction() as conn:
        check_member(conn,ctx,'project:write');p=_get(conn,ctx,project_id)
        if p.revision!=request.expected_revision:fail(409,'PROJECT_VERSION_CONFLICT','项目版本已变化')
        values=request.model_dump(exclude_unset=True);values.pop('expected_revision')
        p=WritingProject.model_validate(p.model_dump()|values|{'revision':p.revision+1,'updated_at':_now()})
        conn.execute('UPDATE projects SET data=?,updated_at=? WHERE id=?',(p.model_dump_json(),p.updated_at,p.id))
        audit(store,conn,ctx,'project.update',p.id,revision=p.revision)
        return p


def refuse_export_or_job(store,ctx,project_id):
    with store.workflow_transaction() as conn:
        check_member(conn,ctx,'project:read');_get(conn,ctx,project_id)
        fail(409,'PROJECT_ASYNC_ISOLATION_PENDING','该租户项目的文件/导出/异步隔离尚未验收，禁止调用兼容入口')
