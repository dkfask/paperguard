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
from app.models.template import CoverBlock, PaperTemplate
from app.models.writing_specification import WritingSpecification
from app.services.document_candidates import (
    CandidateBindingError,
    TEMPLATE_CANDIDATE_KIND,
    bind_candidate_for_confirmation,
    list_candidates,
    load_candidate,
    load_confirmed_candidate_with_record,
    save_confirmed_candidate,
    save_parsed_candidate,
)
from app.services.document_access import can_access_template
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


def require_template_access(store: Store, user: dict, template: PaperTemplate) -> None:
    if not can_access_template(store, user, template):
        raise HTTPException(status_code=404, detail="模板不存在")


def is_candidate_published_template(template: PaperTemplate) -> bool:
    return (template.admission_provenance or {}).get("source") == "document_template_candidate"


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
        if can_access_template(store, user, t)
    ]


@router.post("/admission/assess")
def assess_template_before_save(
    payload: PaperTemplate,
    user: dict = Depends(require_template_manager),
    store: Store = Depends(get_store),
) -> dict:
    """Assess the submitted template without saving or changing any version."""
    existing = store.get_template(payload.id)
    if (existing is not None and can_access_template(store, user, existing)
            and payload.model_dump(mode="json") == existing.model_dump(mode="json")):
        # Restore signed numbers from the saved version after a browser JSON
        # round trip; an edited payload is always assessed as submitted.
        payload = existing
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
    require_template_access(store, user, tpl)
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
    require_template_access(store, manager, template)
    if is_candidate_published_template(template):
        raise HTTPException(status_code=409, detail="正式准入模板为不可变版本，请创建新版本并重新准入")
    path = resolve_template_source(template.source_docx)
    if path is None:
        raise HTTPException(status_code=409, detail="Word 母版不存在，请重新导入")
    try:
        proposed = template.model_copy(deep=True)
        proposed.version = template.version.split("+word.")[0]+"+word."+new_id("version").rsplit("-",1)[-1]
        reviewed = request.model_copy(update={"accept_recommended":True,"accept_new_object_defaults":True,"remove_template_instructions":True})
        proposed = compile_profile(proposed,path,reviewed,actor=manager["id"])
        pending_digits = bool(proposed.source_execution.get("digit_font_review", {}).get("candidates")
                              and not proposed.source_execution.get("digit_font_rule"))
        trials = None if pending_digits else trial_profile(proposed,path)
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
    require_template_access(store, manager, current)
    if is_candidate_published_template(current):
        raise HTTPException(status_code=409, detail="正式准入模板为不可变版本，请创建新版本并重新准入")
    if sorted(request.confirmed_content_rule_ids) != sorted(published.source_execution.get("confirmed_content_rule_ids") or []):
        raise HTTPException(status_code=409,detail="内容规则确认项与预览不同，请重新预览")
    if sorted(request.confirmed_digit_font_rule_ids) != sorted(published.source_execution.get("confirmed_digit_font_rule_ids") or []):
        raise HTTPException(status_code=409,detail="数字字体确认项与预览不同，请重新预览")
    if {key: value.model_dump(mode="json") for key, value in request.inline_label_overrides.items()} != published.source_execution.get("inline_label_overrides", {}):
        raise HTTPException(status_code=409,detail="关键词标签与预览不同，请重新预览")
    if (request.body_page_number.model_dump(mode="json") if request.body_page_number else None) != published.source_execution.get("body_page_number"):
        raise HTTPException(status_code=409,detail="正文页码设置与预览不同，请重新预览")
    if request.defense_date_field_key != str(published.source_execution.get("defense_date_field_key") or ""):
        raise HTTPException(status_code=409,detail="答辩日期字段与预览不同，请重新预览")
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
                  manager: dict = Depends(require_template_manager)) -> PaperTemplate:
    """创建模板。id 未指定时由服务端生成。"""
    data = dict(payload)
    if not str(data.get("id") or "").strip():
        data["id"] = new_id("tpl")
    try:
        template = PaperTemplate.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板数据无效: {exc}") from exc
    _validate_template_structure(template)
    if manager.get("role") == "teacher":
        organization_context = dict(manager.get("organization_context") or {})
        if not organization_context:
            raise HTTPException(status_code=422, detail="教师创建模板前必须绑定学校或组织范围")
        template.organization_context = organization_context
    if not store.insert_template_if_absent(template):
        raise HTTPException(status_code=409, detail="模板 id 已存在")
    return template


@router.post("/default")
def create_default_template(store: Store = Depends(get_store),
                        _: dict = Depends(require_template_manager)) -> PaperTemplate:
    """内置模板存库(幂等)。"""
    tpl = default_template()
    existing = store.get_template(tpl.id)
    if existing and is_candidate_published_template(existing):
        raise HTTPException(status_code=409, detail="正式准入模板为不可变版本，不能重置覆盖")
    if existing is None and store.insert_template_if_absent(tpl):
        return tpl
    if existing is None:
        existing = store.get_template(tpl.id)
        if existing and is_candidate_published_template(existing):
            raise HTTPException(status_code=409, detail="正式准入模板为不可变版本，不能重置覆盖")
        if existing is None:
            raise HTTPException(status_code=409, detail="模板 ID 已被占用，请重试")
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


def _paper_from_candidate(candidate: TemplateParseCandidate, record: dict) -> PaperTemplate:
    paper = paper_from_definition(candidate.template_id, candidate.version, candidate.definition)
    paper.name = candidate.name
    paper.cover_blocks = [CoverBlock.model_validate(block) for block in candidate.definition.cover_blocks]
    paper.organization_context = dict(record.get("organization_context") or {})
    specification = candidate.definition.extensions.get("writing_specification")
    if specification:
        paper.writing_specification = WritingSpecification.model_validate(specification)
    return paper


def _published_template_sha256(template: PaperTemplate) -> str:
    payload = template.model_dump(mode="json")
    payload.pop("updated_at", None)
    return digest(payload)


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
        bound, record = bind_candidate_for_confirmation(store,TEMPLATE_CANDIDATE_KIND,payload.candidate,TemplateParseCandidate,manager)
    except CandidateBindingError as exc:
        raise HTTPException(status_code=404,detail="模板候选不存在或无权访问") from exc
    if bound.status == "confirmed":
        raise HTTPException(status_code=409, detail="已确认的模板候选不可再修改，请重新导入新版本")
    path = resolve_template_source(bound.source_asset.storage_key) if bound.source_asset else None
    if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != bound.source_asset.checksum:
        raise HTTPException(status_code=409,detail="候选母版摘要变化，请重新导入")
    request = payload.confirmation
    if payload.preview_only:
        request = request.model_copy(update={"accept_recommended":True,"accept_new_object_defaults":True,"remove_template_instructions":True})
    try:
        paper = compile_profile(_paper_from_candidate(bound, record),path,request,actor=manager["id"])
        pending_digits = bool(paper.source_execution.get("digit_font_review", {}).get("candidates")
                              and not paper.source_execution.get("digit_font_rule"))
        if pending_digits and not payload.preview_only:
            raise ProfileError("请确认模板中的数字字体规则并重新预览")
        trials = None if pending_digits else trial_profile(paper,path)
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
    if not payload.preview_only:
        if bound.admission_assessment.get("blockers"):
            raise HTTPException(status_code=422, detail={
                "message": "模板存在未解决的准入阻断项，不能保存正式试排证据。",
                "admission": bound.admission_assessment,
            })
        if not trials or any(report.get("status") != "pass" for report in trials.values()):
            raise HTTPException(status_code=422, detail="非预览试排未完整通过，不能保存候选配置")
        evidence = {
            "candidate_id": bound.candidate_id,
            "candidate_definition_sha256": digest(bound.definition.model_dump(mode="json")),
            "source_docx_sha256": bound.source_asset.checksum,
            "source_profile_sha256": paper.source_execution.get("profile_sha256", ""),
            "trial_reports": trials,
            "trial_reports_sha256": digest(trials),
            "template_snapshot": paper.model_dump(mode="json"),
            "prepared_at": _now(),
            "prepared_by": str(manager.get("id") or ""),
        }
        evidence["evidence_sha256"] = digest(evidence)
        updated_record = dict(record)
        updated_record["candidate"] = bound.model_dump(mode="json")
        updated_record["prepared_trial_evidence"] = evidence
        if store.save_workflow_record(
            TEMPLATE_CANDIDATE_KIND,
            updated_record,
            expected_revision=int(record.get("revision", 0)),
        ) is None:
            raise HTTPException(status_code=409, detail="模板候选已变化，请重新加载后试排")
    return {"candidate":bound.model_dump(mode="json"),"template":paper.model_dump(mode="json"),"trials":trials}


class CandidatePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str


@router.post("/parse/publish", dependencies=[Depends(require_general_document_platform)])
def publish_docx_template_candidate(
    payload: CandidatePublishRequest,
    manager: dict = Depends(require_manager),
    store: Store = Depends(get_store),
) -> dict:
    try:
        candidate, record = load_confirmed_candidate_with_record(
            store, TEMPLATE_CANDIDATE_KIND, payload, TemplateParseCandidate, manager
        )
    except CandidateBindingError as exc:
        raise HTTPException(status_code=404, detail="模板候选不存在、尚未确认或无权访问") from exc

    evidence = record.get("prepared_trial_evidence")
    if not isinstance(evidence, dict):
        raise HTTPException(status_code=409, detail="缺少非预览试排证据，不能发布模板")
    evidence_body = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    if evidence.get("evidence_sha256") != digest(evidence_body):
        raise HTTPException(status_code=409, detail="候选试排证据摘要无效，请重新试排")
    if evidence.get("candidate_id") != candidate.candidate_id:
        raise HTTPException(status_code=409, detail="试排证据与候选身份不匹配")
    if evidence.get("candidate_definition_sha256") != digest(candidate.definition.model_dump(mode="json")):
        raise HTTPException(status_code=409, detail="候选内容在试排后发生变化，请重新试排")
    if digest(evidence.get("trial_reports") or {}) != evidence.get("trial_reports_sha256"):
        raise HTTPException(status_code=409, detail="试排报告摘要无效，请重新试排")

    asset = candidate.source_asset
    source_path = resolve_template_source(asset.storage_key) if asset else None
    if (asset is None or source_path is None
            or candidate.definition.source_docx != asset.storage_key
            or hashlib.sha256(source_path.read_bytes()).hexdigest() != asset.checksum
            or evidence.get("source_docx_sha256") != asset.checksum):
        raise HTTPException(status_code=409, detail="模板源文件缺失或摘要已变化，请重新导入")
    trials = evidence.get("trial_reports")
    if (not isinstance(trials, dict) or set(trials) != {"normal", "long"}
            or any(not isinstance(report, dict)
                   or report.get("status") != "pass"
                   or report.get("source_profile_sha256") != evidence.get("source_profile_sha256")
                   or not report.get("output_sha256")
                   or int(report.get("check_count", 0)) <= 0
                   for report in trials.values())):
        raise HTTPException(status_code=409, detail="正常稿或长内容稿试排未通过，请重新试排")

    try:
        template = PaperTemplate.model_validate(evidence.get("template_snapshot") or {})
        profile = verify_profile(template, source_path.read_bytes())
    except Exception as exc:
        raise HTTPException(status_code=409, detail="签名执行配置或试排模板快照无效，请重新确认") from exc
    if (template.id != candidate.template_id
            or template.version != candidate.version
            or template.source_docx != asset.storage_key
            or template.source_execution != candidate.definition.extensions.get("source_execution")
            or profile.get("profile_sha256") != evidence.get("source_profile_sha256")):
        raise HTTPException(status_code=409, detail="试排模板与已确认候选或源文件不一致")

    refreshed = _refresh_candidate_admission(candidate)
    if refreshed.admission_assessment.get("blockers"):
        raise HTTPException(status_code=422, detail={
            "message": "模板仍有未解决的准入阻断项，不能发布。",
            "admission": refreshed.admission_assessment,
        })
    admission = template_admission(template)
    if admission.get("blockers") or not admission.get("can_use_for_draft"):
        raise HTTPException(status_code=422, detail={
            "message": "模板发布后的准入复核未通过。",
            "admission": admission,
        })

    publication = record.get("publication")
    if publication and publication.get("status") == "published":
        existing = store.get_template(candidate.template_id)
        if (existing is None
                or publication.get("template_sha256") != _published_template_sha256(existing)):
            raise HTTPException(status_code=409, detail="已发布模板与发布记录不一致，需管理员复核")
        return {"template": existing.model_dump(mode="json"), "admission": template_admission(existing)}
    if publication and publication.get("status") != "publishing":
        raise HTTPException(status_code=409, detail="候选发布状态异常，请管理员复核")

    existing = store.get_template(candidate.template_id)
    if publication is None and existing is not None:
        raise HTTPException(status_code=409, detail="模板 ID 已被其他模板占用，不能覆盖")
    if manager.get("role") != "admin" and not template.organization_context:
        raise HTTPException(status_code=422, detail="教师发布模板前必须绑定学校或组织范围")

    if publication is None:
        publication = {
            "status": "publishing",
            "template_id": candidate.template_id,
            "published_by": str(manager.get("id") or manager.get("username") or ""),
            "published_at": _now(),
        }
        template.created_at = publication["published_at"]
    else:
        if publication.get("template_id") != candidate.template_id:
            raise HTTPException(status_code=409, detail="发布记录指向不同模板，需管理员复核")
        template.created_at = publication.get("published_at", "")

    template.admission_provenance = {
        "source": "document_template_candidate",
        "visibility": "global" if manager.get("role") == "admin" and not template.organization_context else "organization",
        "published_by_id": str(manager.get("id") or ""),
        "candidate_id": candidate.candidate_id,
        "candidate_version": candidate.version,
        "source_docx_sha256": asset.checksum,
        "profile_sha256": profile["profile_sha256"],
        "assessment_hash": admission.get("assessment_hash", ""),
        "trial_evidence_sha256": evidence["evidence_sha256"],
        "confirmed_by": candidate.confirmed_by,
        "confirmed_at": candidate.confirmed_at,
        "published_by": publication["published_by"],
        "published_at": publication["published_at"],
    }
    template_sha256 = _published_template_sha256(template)
    if publication.get("template_sha256") not in (None, template_sha256):
        raise HTTPException(status_code=409, detail="发布模板快照摘要发生变化，需管理员复核")
    publication["template_sha256"] = template_sha256

    if record.get("publication") is None:
        updated_record = dict(record)
        updated_record["publication"] = publication
        record = store.save_workflow_record(
            TEMPLATE_CANDIDATE_KIND,
            updated_record,
            expected_revision=int(record.get("revision", 0)),
        )
        if record is None:
            raise HTTPException(status_code=409, detail="候选已由其他操作更新，请重试")

    if store.insert_template_if_absent(template):
        saved_template = template
    else:
        existing = store.get_template(candidate.template_id)
        if existing is None or _published_template_sha256(existing) != template_sha256:
            rollback_record = dict(record)
            rollback_record.pop("publication", None)
            store.save_workflow_record(
                TEMPLATE_CANDIDATE_KIND,
                rollback_record,
                expected_revision=int(record.get("revision", 0)),
            )
            raise HTTPException(status_code=409, detail="模板 ID 在发布期间被占用，未覆盖现有模板；请重新导入后重试")
        saved_template = existing
    if _published_template_sha256(saved_template) != template_sha256:
        raise HTTPException(status_code=409, detail="模板写入后摘要不一致，发布保持待复核")

    admission = template_admission(saved_template)
    if admission.get("blockers") or not admission.get("can_use_for_draft"):
        raise HTTPException(status_code=409, detail={
            "message": "模板持久化后的复核未通过，发布保持待复核。",
            "admission": admission,
        })

    updated_record = dict(record)
    updated_record["publication"] = {**publication, "status": "published"}
    if store.save_workflow_record(
        TEMPLATE_CANDIDATE_KIND,
        updated_record,
        expected_revision=int(record.get("revision", 0)),
    ) is None:
        raise HTTPException(status_code=409, detail="模板已写入，发布状态待复核；请重试以完成对账")

    store.save_audit_event({
        "id": new_id("audit"),
        "actor_id": str(manager.get("id") or ""),
        "action": "template.candidate.published",
        "resource_type": "template_candidate",
        "resource_id": candidate.candidate_id,
        "organization_id": str((manager.get("organization_context") or {}).get("school_id") or ""),
        "result": "success",
        "created_at": publication["published_at"],
    })
    return {"template": saved_template.model_dump(mode="json"), "admission": admission}


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
        # JavaScript serializes integral floats as integers. Restore the
        # server-owned, trialled definition when the submitted values are
        # equal, so that this harmless JSON round trip does not invalidate its
        # signed profile or the bound definition digest. Real changes still
        # go through the normal admission and publication checks.
        if record.get("prepared_trial_evidence"):
            prepared = TemplateParseCandidate.model_validate(record.get("candidate") or {})
            if bound.definition.model_dump(mode="json") == prepared.definition.model_dump(mode="json"):
                bound = bound.model_copy(update={"definition": prepared.definition})
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
                              user: dict = Depends(current_user)) -> dict:
    """Return source evidence and its readable projection, never an approval claim."""
    template = store.get_template(tpl_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    require_template_access(store, user, template)
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
                    manager: dict = Depends(require_template_manager)) -> PaperTemplate:
    existing = store.get_template(tpl_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    require_template_access(store, manager, existing)
    if is_candidate_published_template(existing):
        raise HTTPException(status_code=409, detail="正式准入模板为不可变版本，请创建新版本并重新准入")
    _validate_template_structure(payload)
    payload.id = tpl_id
    payload.organization_context = existing.organization_context
    payload.admission_provenance = existing.admission_provenance
    return store.save_template(payload)


@router.delete("/{tpl_id}", status_code=204)
def delete_template(tpl_id: str, store: Store = Depends(get_store),
                    manager: dict = Depends(require_template_manager)) -> None:
    tpl = store.get_template(tpl_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    require_template_access(store, manager, tpl)
    if is_candidate_published_template(tpl):
        raise HTTPException(status_code=409, detail="正式准入模板不能删除，请创建新版本并重新准入")
    if any(project.template_id == tpl_id for project in store.list_projects()):
        raise HTTPException(status_code=409, detail="模板仍被论文项目使用, 无法删除")
    if not store.delete_template(tpl_id):
        raise HTTPException(status_code=404, detail="模板不存在")
    delete_template_source(tpl.source_docx)


def _count_sections(nodes) -> int:
    return sum(1 + _count_sections(n.children) for n in nodes)
