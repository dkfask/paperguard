"""模板管理 API: 章节结构 + 样式规范在线配置 + Word 导入。"""

from __future__ import annotations

import shutil
import tempfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict

from app.auth import current_user, require_manager, require_template_manager
from app.core.feature_flags import require_general_document_platform
from app.core.extract.specification import extract_writing_specification, render_specification_markdown

from app.db import Store, new_id
from app.domain.documents.schemas import TemplateAsset
from app.models.template import PaperTemplate
from app.models.writing_specification import WritingSpecification
from app.services.document_candidates import (
    CandidateBindingError,
    TEMPLATE_CANDIDATE_KIND,
    bind_candidate_for_confirmation,
    list_candidates,
    load_candidate,
    save_confirmed_candidate,
    save_parsed_candidate,
)
from app.services.docx_package_fixer import strip_comments_from_docx
from app.services.import_template import (
    extract_cover_blocks,
    extract_meta_fields,
    extract_section_tree,
    import_template_styles,
)
from app.services.legacy_word import LegacyWordConversionError, prepare_docx
from app.services.projects import default_template, seed_default_template
from app.services.template_source import (
    delete_template_source,
    resolve_template_source,
    save_template_source,
)
from app.services.template_admission import assess_paper_template
from app.services.template_engine import (
    TemplateCandidateError,
    TemplateParseCandidate,
    confirm_template_candidate,
    parse_docx_template as parse_template_candidate,
)
from app.services.upload_security import IMPORT_MAX_BYTES, read_limited, validate_docx_bytes
from app.services.source_template_profile import ConfirmWritingProfile, ProfileError, compile_profile, digest, execution_basis, verify_profile, paper_from_definition, flatten, trial_profile

router = APIRouter(prefix="/api/templates")
_WORD_TEMPLATE_EXTENSIONS = {".doc", ".docx"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_store() -> Store:
    store = Store()
    seed_default_template(store)
    return store


def template_admission(template: PaperTemplate) -> dict:
    source_path = resolve_template_source(template.source_docx) if template.source_docx else None
    source_data = source_path.read_bytes() if source_path is not None else None
    return assess_paper_template(template, source_data)


def _extract_source_specification(path: Path) -> WritingSpecification:
    try:
        return extract_writing_specification(path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="无法从 Word 母版提取规范，请核对文件结构与解析资源限制") from exc


def ensure_template_admitted_for_writing(template: PaperTemplate) -> dict:
    assessment = template_admission(template)
    if not assessment["can_use_for_draft"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "template_admission_blocked",
                "message": "此模板含有当前检查器无法覆盖的结构，暂不能用于新论文。请先修订或重新导入模板。",
                "admission": assessment,
            },
        )
    return assessment


@router.get("")
def list_templates(
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict]:
    """模板列表(不含章节树细节)。

    模板是学校内部论文资料，此前这里连 Depends(current_user) 都没有，
    未登录即可枚举全部模板；/api/templates/{id} 同理，且会返回完整章节树、
    排版样式、封面块与源 Word 文档元数据。
    前端四个调用点(Templates/Writer/Cohorts/Workflow)全部在登录后，
    登录与注册页不使用模板，因此补认证不影响任何功能。
    """
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "updated_at": t.updated_at,
            "section_count": _count_sections(t.sections),
            "version": t.version,
            "rule_packs": t.rule_packs,
            "requirement_set_ids": t.requirement_set_ids,
            "admission": template_admission(t),
        }
        for t in store.list_templates()
    ]


@router.post("/admission/assess")
def assess_template_before_save(
    payload: PaperTemplate,
    _: dict = Depends(require_template_manager),
) -> dict:
    """Assess the submitted template without saving or changing any version."""
    return template_admission(payload)


@router.get("/{tpl_id}")
def get_template(
    tpl_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> PaperTemplate:
    tpl = store.get_template(tpl_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl


def _validate_template_structure(payload: PaperTemplate) -> None:
    # Publication is the only route that may install an approved execution profile.
    payload.source_execution = {}
    keys: set[str] = set()
    def walk(nodes) -> None:
        for node in nodes:
            if node.key in keys:
                raise HTTPException(status_code=422, detail=f"章节 key 重复: {node.key}")
            if not node.title.strip():
                raise HTTPException(status_code=422, detail=f"章节标题不能为空: {node.key}")
            keys.add(node.key)
            walk(node.children)
    walk(payload.sections)
    fields: set[str] = set()
    for field in payload.meta_fields:
        if not field.key.strip() or not field.label.strip():
            raise HTTPException(status_code=422, detail="封面字段 key 和显示名不能为空")
        if field.key in fields:
            raise HTTPException(status_code=422, detail=f"封面字段 key 重复: {field.key}")
        fields.add(field.key)
    if payload.source_docx and resolve_template_source(payload.source_docx) is None:
        raise HTTPException(status_code=422, detail="模板源文件不存在或已失效, 请重新导入 Word 模板")
    # A browser can review extracted evidence, but cannot fabricate trusted source facts.
    source_path = resolve_template_source(payload.source_docx) if payload.source_docx else None
    payload.writing_specification = _extract_source_specification(source_path) if source_path else None
    if len(payload.rule_packs) != len(set(payload.rule_packs)):
        raise HTTPException(status_code=422, detail="规则包不能重复")
    if len(payload.requirement_set_ids) != len(set(payload.requirement_set_ids)):
        raise HTTPException(status_code=422, detail="规范集合不能重复")


@router.post("/{tpl_id}/writing-profile/propose")
def propose_writing_profile(tpl_id: str, request: ConfirmWritingProfile,
                            store: Store = Depends(get_store), manager: dict = Depends(require_template_manager)) -> dict:
    template = store.get_template(tpl_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    path = resolve_template_source(template.source_docx)
    if path is None:
        raise HTTPException(status_code=409, detail="Word 母版不存在，请重新导入")
    try:
        proposed = template.model_copy(deep=True)
        proposed.version = template.version.split("+word.")[0]+"+word."+new_id("version").rsplit("-",1)[-1]
        reviewed = request.model_copy(update={"accept_recommended":True,"accept_new_object_defaults":True,"remove_template_instructions":True})
        proposed = compile_profile(proposed,path,reviewed,actor=manager["id"])
        trials = trial_profile(proposed,path)
    except ProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    proposal_id = new_id("writingprofile")
    store.save_workflow_record("writing_profile_proposal",{"id":proposal_id,"owner_id":manager["id"],"template_id":tpl_id,
        "parent_basis_sha256":digest(execution_basis(template)),"template":proposed.model_dump(mode="json"),"trials":trials})
    return {"proposal_id":proposal_id,"template":proposed.model_dump(mode="json"),"trials":trials}


@router.post("/{tpl_id}/publish-writing-profile")
def publish_writing_profile(tpl_id: str, request: ConfirmWritingProfile,
                            store: Store = Depends(get_store), manager: dict = Depends(require_template_manager)) -> dict:
    proposal = store.get_workflow_record("writing_profile_proposal",request.proposal_id)
    if not proposal or proposal.get("owner_id") != manager["id"] or proposal.get("template_id") != tpl_id:
        raise HTTPException(status_code=409,detail="请先预览本人准备的母版执行配置")
    if not (request.accept_recommended and request.accept_new_object_defaults and request.remove_template_instructions):
        raise HTTPException(status_code=422,detail="请完成格式、说明移除与新增图表策略的确认")
    published = PaperTemplate.model_validate(proposal["template"])
    if published.source_execution.get("removed_toc_entries") and not request.remove_unmatched_toc_entries:
        raise HTTPException(status_code=422,detail="目录中有无对应正文的条目，请确认清理后再发布")
    current = store.get_template(tpl_id)
    if current is None:
        raise HTTPException(status_code=404,detail="模板不存在")
    if sorted(request.confirmed_content_rule_ids) != sorted(published.source_execution.get("confirmed_content_rule_ids") or []):
        raise HTTPException(status_code=409,detail="内容规则确认项与预览不同，请重新预览")
    if request.preserve_manuscript_structure != bool(published.source_execution.get("preserve_manuscript_structure")):
        raise HTTPException(status_code=409, detail="章节结构方式与预览不同，请重新预览")
    if current.source_execution.get("profile_sha256") == published.source_execution["profile_sha256"]:
        return {"template":current.model_dump(mode="json"),"admission":template_admission(current)}
    if digest(execution_basis(current)) != proposal["parent_basis_sha256"]:
        raise HTTPException(status_code=409,detail="模板已修改，请重新预览后确认")
    if request.expected_specification_sha256 != published.source_execution["specification_sha256"] or request.overrides != published.source_execution["overrides"]:
        raise HTTPException(status_code=409,detail="规范或覆盖项与预览不同，请重新预览")
    path = resolve_template_source(published.source_docx)
    try:
        verify_profile(published,path.read_bytes() if path else None)
    except Exception as exc:
        raise HTTPException(status_code=409,detail="母版或执行配置已变化，请重新导入确认") from exc
    store.save_template(published)
    # The proposal keeps the immutable published version without changing the schema ledger.
    proposal["published_at"] = _now()
    store.save_workflow_record("writing_profile_proposal",proposal)
    return {"template":published.model_dump(mode="json"),"admission":template_admission(published)}



@router.post("", status_code=201)
def create_template(payload: dict, store: Store = Depends(get_store),
                  _: dict = Depends(require_template_manager)) -> PaperTemplate:
    """创建模板。id 未指定时由服务端生成。"""
    data = dict(payload)
    if not str(data.get("id") or "").strip():
        data["id"] = new_id("tpl")
    try:
        template = PaperTemplate.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板数据无效: {exc}") from exc
    _validate_template_structure(template)
    if store.get_template(template.id):
        raise HTTPException(status_code=409, detail="模板 id 已存在")
    return store.save_template(template)


@router.post("/default")
def create_default_template(store: Store = Depends(get_store),
                        _: dict = Depends(require_template_manager)) -> PaperTemplate:
    """内置模板存库(幂等)。"""
    tpl = default_template()
    store.save_template(tpl)
    return tpl


@router.post("/strip-comments")
async def strip_template_comments(file: UploadFile = File(...),
                                  _: dict = Depends(require_template_manager)):
    """生成删除批注后的模板副本，不修改上传原文件。"""
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in _WORD_TEMPLATE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只支持 .doc 或 .docx 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-strip-comments-"))
    output = tmp_dir / "template-no-comments.docx"
    try:
        data = read_limited(file, IMPORT_MAX_BYTES)
        source = prepare_docx(data, filename, tmp_dir).path
        validate_docx_bytes(source.read_bytes())
        counters = strip_comments_from_docx(source, output)
        stem = Path(filename).stem
        filename = f"{stem}-no-comments.docx"
        return StreamingResponse(
            output.open("rb"),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-PaperGuard-Removed-Comment-Parts": str(counters["parts"]),
                "X-PaperGuard-Removed-Comment-Anchors": str(counters["anchors"]),
            },
            background=BackgroundTask(shutil.rmtree, tmp_dir, True),
        )
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except LegacyWordConversionError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"批注清理失败: {exc}") from exc


@router.post("/parse", response_model=TemplateParseCandidate, dependencies=[Depends(require_general_document_platform)])
async def parse_docx_template_candidate(file: UploadFile = File(...),
                                        manager: dict = Depends(require_manager),
                                        store: Store = Depends(get_store)) -> TemplateParseCandidate:
    """Parse an uploaded Word document into a generic candidate for human confirmation."""
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in _WORD_TEMPLATE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只支持 .doc 或 .docx 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-template-engine-"))
    try:
        data = read_limited(file, IMPORT_MAX_BYTES)
        path = prepare_docx(data, filename, tmp_dir).path
        validate_docx_bytes(path.read_bytes())
        candidate_id = new_id("tplcand")
        template_id = new_id("tpl")
        candidate = parse_template_candidate(
            path,
            candidate_id=candidate_id,
            template_id=template_id,
            filename=filename,
            name=Path(filename).stem,
        )
        source_docx = save_template_source(path.read_bytes(), preserve_original=True)
        stored_path = resolve_template_source(source_docx)
        if stored_path is None:
            raise HTTPException(status_code=422, detail="模板源文件保存失败")
        stored_data = stored_path.read_bytes()
        source_asset = TemplateAsset(
            id=f"{candidate_id}:source",
            storage_key=source_docx,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=len(stored_data),
            checksum=hashlib.sha256(stored_data).hexdigest(),
            purpose="template-source",
            scan_status="validated",
        )
        definition = candidate.definition.model_copy(update={"source_docx": source_docx})
        from app.services.template_admission import assess_template_definition
        candidate = candidate.model_copy(update={
            "definition": definition,
            "source_asset": source_asset,
            "admission_assessment": assess_template_definition(
                template_id=candidate.template_id,
                version=candidate.version,
                definition=definition,
                source_data=stored_data,
                expected_source_sha256=source_asset.checksum,
            ),
        })
        store.save_audit_event({
            "id": f"audit-{candidate_id}-parsed",
            "actor_id": str(manager.get("id") or ""),
            "action": "template.candidate.parsed",
            "resource_type": "template_candidate",
            "resource_id": candidate_id,
            "organization_id": str((manager.get("organization_context") or {}).get("school_id") or ""),
            "result": "success",
            "created_at": _now(),
        })
        save_parsed_candidate(store, TEMPLATE_CANDIDATE_KIND, candidate, manager)
        return candidate
    except HTTPException:
        raise
    except LegacyWordConversionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板解析失败: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/parse/candidates", response_model=list[TemplateParseCandidate], dependencies=[Depends(require_general_document_platform)])
def list_docx_template_candidates(
    manager: dict = Depends(require_manager),
    store: Store = Depends(get_store),
) -> list[TemplateParseCandidate]:
    """List recent template candidates available for manager review."""
    candidates = list_candidates(store, TEMPLATE_CANDIDATE_KIND, TemplateParseCandidate, manager)
    return [_refresh_candidate_admission(candidate) for candidate in candidates]


def _refresh_candidate_admission(candidate: TemplateParseCandidate) -> TemplateParseCandidate:
    from app.services.template_admission import assess_template_candidate
    source_path = resolve_template_source(candidate.source_asset.storage_key) if candidate.source_asset else None
    definition = candidate.definition
    if source_path and candidate.source_asset.checksum == hashlib.sha256(source_path.read_bytes()).hexdigest():
        specification = _extract_source_specification(source_path)
        definition = definition.model_copy(update={"extensions": {
            **definition.extensions, "writing_specification": specification.model_dump(mode="json"),
            "writing_specification_markdown": render_specification_markdown(specification),
        }})
    refreshed = candidate.model_copy(update={"definition": definition})
    return refreshed.model_copy(update={"admission_assessment": assess_template_candidate(refreshed)})


@router.get("/parse/candidates/{candidate_id}", response_model=TemplateParseCandidate, dependencies=[Depends(require_general_document_platform)])
def get_docx_template_candidate(
    candidate_id: str,
    manager: dict = Depends(require_manager),
    store: Store = Depends(get_store),
) -> TemplateParseCandidate:
    """Restore one scoped template candidate after a page refresh."""
    try:
        candidate = load_candidate(store, TEMPLATE_CANDIDATE_KIND, candidate_id, TemplateParseCandidate, manager)
        return _refresh_candidate_admission(candidate)
    except CandidateBindingError as exc:
        raise HTTPException(status_code=404, detail="模板候选不存在或无权访问") from exc


@router.post("/parse/assess", response_model=TemplateParseCandidate, dependencies=[Depends(require_general_document_platform)])
def assess_docx_template_candidate(
    candidate: TemplateParseCandidate,
    manager: dict = Depends(require_manager),
    store: Store = Depends(get_store),
) -> TemplateParseCandidate:
    """Return a fresh assessment for an edited candidate without persisting it."""
    try:
        bound, _record = bind_candidate_for_confirmation(
            store, TEMPLATE_CANDIDATE_KIND, candidate, TemplateParseCandidate, manager
        )
        return _refresh_candidate_admission(bound)
    except CandidateBindingError as exc:
        raise HTTPException(status_code=404, detail="模板候选不存在或无权访问") from exc


class CandidateWritingProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: TemplateParseCandidate
    confirmation: ConfirmWritingProfile
    preview_only: bool = True


@router.post("/parse/writing-profile", dependencies=[Depends(require_general_document_platform)])
def prepare_candidate_writing_profile(payload: CandidateWritingProfileRequest,
                                      manager: dict = Depends(require_manager), store: Store = Depends(get_store)) -> dict:
    try:
        bound, _ = bind_candidate_for_confirmation(store,TEMPLATE_CANDIDATE_KIND,payload.candidate,TemplateParseCandidate,manager)
    except CandidateBindingError as exc:
        raise HTTPException(status_code=404,detail="模板候选不存在或无权访问") from exc
    path = resolve_template_source(bound.source_asset.storage_key) if bound.source_asset else None
    if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != bound.source_asset.checksum:
        raise HTTPException(status_code=409,detail="候选母版摘要变化，请重新导入")
    request = payload.confirmation
    if payload.preview_only:
        request = request.model_copy(update={"accept_recommended":True,"accept_new_object_defaults":True,"remove_template_instructions":True})
    try:
        paper = compile_profile(paper_from_definition(bound.template_id,bound.version,bound.definition),path,request,actor=manager["id"])
        trials = trial_profile(paper,path)
    except ProfileError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    from app.domain.documents.schemas import StyleDefinition
    from app.domain.documents.mappers import paper_template_to_domain
    _,compiled_version = paper_template_to_domain(paper)
    original_nodes = {}
    def collect(nodes):
        for node in nodes:
            original_nodes[node.key]=node
            collect(node.children)
    collect(bound.definition.sections)
    def preserve(nodes):
        for node in nodes:
            if node.key in original_nodes:
                old=original_nodes[node.key]
                node.extensions=old.extensions
                node.content_schema=old.content_schema
                node.allow_user_add=old.allow_user_add
                node.allow_user_delete=old.allow_user_delete
            if node.kind in {"keywords","english_keywords"}:
                node.allow_user_add=False
                node.allow_user_delete=False
                node.content_schema={"allowed_kinds":["paragraph"],"single_line":True}
            preserve(node.children)
    preserve(compiled_version.definition.sections)
    bound.definition.sections=compiled_version.definition.sections
    bound.definition.styles = {key:StyleDefinition.model_validate(style.model_dump(mode="json")) for key,style in paper.styles.items()}
    bound.definition.allow_structure_edit = False
    bound.definition.extensions["source_execution"] = dict(paper.source_execution)
    if payload.preview_only:
        # A preview cannot be replayed as an approved publication.
        bound.definition.extensions["source_execution"].pop("signature",None)
        paper.source_execution.pop("signature",None)
    bound = _refresh_candidate_admission(bound)
    return {"candidate":bound.model_dump(mode="json"),"template":paper.model_dump(mode="json"),"trials":trials}


@router.post("/parse/confirm", response_model=TemplateParseCandidate, dependencies=[Depends(require_general_document_platform)])
def confirm_docx_template_candidate(candidate: TemplateParseCandidate,
                                    manager: dict = Depends(require_manager),
                                    store: Store = Depends(get_store)) -> TemplateParseCandidate:
    """Record human confirmation on a candidate; publishing is a later phase."""
    try:
        bound, record = bind_candidate_for_confirmation(
            store,
            TEMPLATE_CANDIDATE_KIND,
            candidate,
            TemplateParseCandidate,
            manager,
        )
        bound = _refresh_candidate_admission(bound)
        admission = bound.admission_assessment
        if admission.get("blockers"):
            raise HTTPException(status_code=422, detail={
                "message": "模板含有当前导出检查器无法覆盖的结构，不能确认或发布。",
                "admission": admission,
            })
        confirmed = confirm_template_candidate(
            bound,
            confirmed_by=str(manager.get("id") or manager.get("username") or ""),
            confirmed_at=_now(),
        )
        save_confirmed_candidate(store, TEMPLATE_CANDIDATE_KIND, confirmed, record)
        store.save_audit_event({
            "id": new_id("audit"),
            "actor_id": str(manager.get("id") or ""),
            "action": "template.candidate.confirmed",
            "resource_type": "template_candidate",
            "resource_id": candidate.candidate_id,
            "organization_id": str((manager.get("organization_context") or {}).get("school_id") or ""),
            "result": "success",
            "created_at": confirmed.confirmed_at,
        })
        return confirmed
    except CandidateBindingError as exc:
        raise HTTPException(status_code=404, detail="模板候选不存在或无权访问") from exc
    except HTTPException:
        raise
    except TemplateCandidateError as exc:
        store.save_audit_event({
            "id": new_id("audit"),
            "actor_id": str(manager.get("id") or ""),
            "action": "template.candidate.confirmation_failed",
            "resource_type": "template_candidate",
            "resource_id": candidate.candidate_id,
            "organization_id": str((manager.get("organization_context") or {}).get("school_id") or ""),
            "result": "failure",
            "reason": "blocking_candidate_issues",
            "created_at": _now(),
        })
        raise HTTPException(
            status_code=422,
            detail={
                "message": "模板候选不能确认",
                "issues": [issue.model_dump(mode="json") for issue in exc.issues],
            },
        ) from exc


@router.post("/import")
async def import_docx_template(file: UploadFile = File(...),
                              _: dict = Depends(require_template_manager)) -> dict:
    """上传 Word 文档, 识别字体/字号/行距/缩进等样式, 返回可生成模板的配置。"""
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in _WORD_TEMPLATE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只支持 .doc 或 .docx 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-import-"))
    try:
        data = read_limited(file, IMPORT_MAX_BYTES)
        path = prepare_docx(data, filename, tmp_dir).path
        validate_docx_bytes(path.read_bytes())
        result = import_template_styles(path)
        # 识别结果合并默认样式: 未识别到的键保留默认值, 保证返回完整样式集
        merged = {k: v.model_dump() for k, v in default_template().styles.items()}
        merged.update({k: v.model_dump() for k, v in result["styles"].items()})
        # 识别章节结构 (Heading 段落 → 章节树)
        sections = extract_section_tree(path)
        meta_fields = [f.model_dump() for f in extract_meta_fields(path)]
        cover_blocks = [b.model_dump() for b in extract_cover_blocks(path)]
        # 留档模板源文件: 导出时以它为排版基底, 完整继承 styles/页眉页脚/页边距
        writing_specification = extract_writing_specification(path)
        source_docx = save_template_source(path.read_bytes(), preserve_original=True)
        imported_sections = sections or default_template().sections
        preview_template = PaperTemplate.model_validate({
            **default_template().model_dump(mode="json"),
            "id": "template-import-preview",
            "name": Path(filename).stem or "导入模板",
            "sections": imported_sections,
            "meta_fields": meta_fields,
            "cover_blocks": cover_blocks,
            "source_docx": source_docx,
            "styles": merged,
            "writing_specification": writing_specification,
        })
        return {
            "filename": filename,
            **result,
            "styles": merged,
            "sections": sections,
            "meta_fields": meta_fields,
            "cover_blocks": cover_blocks,
            "source_docx": source_docx,
            "writing_specification": writing_specification.model_dump(mode="json"),
            "writing_specification_markdown": render_specification_markdown(writing_specification),
            "admission": template_admission(preview_template),
        }
    except HTTPException:
        raise
    except LegacyWordConversionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板识别失败: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/{tpl_id}/writing-specification")
def get_writing_specification(tpl_id: str, store: Store = Depends(get_store),
                              _: dict = Depends(current_user)) -> dict:
    """Return source evidence and its readable projection, never an approval claim."""
    template = store.get_template(tpl_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    source = resolve_template_source(template.source_docx) if template.source_docx else None
    if source is None:
        raise HTTPException(status_code=409, detail="此模板没有可解析的 Word 母版，请先上传 Word 模板")
    source_data = source.read_bytes()
    if template.writing_specification and template.writing_specification.source_sha256 != hashlib.sha256(source_data).hexdigest():
        raise HTTPException(status_code=409, detail="Word 母版摘要已变化，请重新导入并确认规范")
    specification = template.writing_specification or _extract_source_specification(source)
    return {"template_id": template.id, "version": template.version,
            "specification": specification.model_dump(mode="json"),
            "markdown": render_specification_markdown(specification)}


@router.put("/{tpl_id}")
def update_template(tpl_id: str, payload: PaperTemplate,
                    store: Store = Depends(get_store),
                    _: dict = Depends(require_template_manager)) -> PaperTemplate:
    _validate_template_structure(payload)
    if store.get_template(tpl_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    payload.id = tpl_id
    return store.save_template(payload)


@router.delete("/{tpl_id}", status_code=204)
def delete_template(tpl_id: str, store: Store = Depends(get_store),
                    _: dict = Depends(require_template_manager)) -> None:
    tpl = store.get_template(tpl_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if any(project.template_id == tpl_id for project in store.list_projects()):
        raise HTTPException(status_code=409, detail="模板仍被论文项目使用, 无法删除")
    if not store.delete_template(tpl_id):
        raise HTTPException(status_code=404, detail="模板不存在")
    delete_template_source(tpl.source_docx)


def _count_sections(nodes) -> int:
    return sum(1 + _count_sections(n.children) for n in nodes)
