"""
提示词管理 API 路由
- 词库增删改查
- CSV/JSON 导入
- 按词库创建批量生图任务（不再动态生成提示词）
"""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import CROWD_TYPES, SINGLE_TYPES
from app.core.database import SessionLocal, get_db
from app.core.settings_resolver import get_setting_value
from app.models.database import BaseImage, GenerateTask, PromptTemplate
from app.schemas.common import BaseResponse, PromptCreateRequest, PromptGenerateRequest
from app.services import progress_store as ps

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_TYPE = "prompt"
VALID_ENGINES = {"seedream", "nanobanana"}


def _normalize_engine(value: Optional[str], fallback: str = "seedream") -> str:
    engine = (value or fallback).strip().lower()
    if engine not in VALID_ENGINES:
        return fallback
    return engine


def _clamp_reference_weight(value: Optional[int], default: int = 80) -> int:
    if value is None:
        return default
    return max(0, min(100, int(value)))


def _normalize_active(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y", "on", "是", "启用"}:
        return True
    if token in {"0", "false", "no", "n", "off", "否", "停用"}:
        return False
    return default


def _build_task_prompt(base_prompt: str, style_name: str) -> str:
    """
    统一任务约束：
    - 严格参考底图背景/光影/机位
    - 仅修改人物主体穿搭与造型
    """
    base = (base_prompt or "").strip()
    guard = (
        "严格参考上传底图的背景、景点地标、色调、光影、机位和构图，不得改换地点或背景。"
        f"只改变人物主体穿搭与造型，按“{style_name}”执行；可调整发型、配饰、姿态与景别。"
        "不要沿用原图人物身份，不要新增多余人物。"
        "优先体现服装层次、材质、版型和整体气质。"
    )
    return f"{guard} {base}" if base else guard


def _build_task_negative_prompt(base_negative: str) -> str:
    extra = (
        "背景替换, 地标变更, 构图改变, 光影方向改变, 增加多人物, 沿用原图人物身份, "
        "脸部遮挡, 墨镜, 口罩, 手挡脸, 头发遮眼, 大面积涂抹感, 低清晰度"
    )
    base = (base_negative or "").strip()
    return f"{base}, {extra}" if base else extra


def _parse_csv_rows(content: str) -> tuple[list[dict[str, Any]], list[str]]:
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return [], ["CSV 缺少表头"]

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, row in enumerate(reader, start=2):
        normalized = {str(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not any(normalized.values()):
            continue
        normalized["_row_index"] = idx
        rows.append(normalized)
    return rows, errors


def _parse_json_rows(content: str) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    try:
        payload = json.loads(content)
    except Exception as e:
        return [], [f"JSON 解析失败: {e}"]

    if isinstance(payload, dict):
        data = payload.get("rows", [])
    elif isinstance(payload, list):
        data = payload
    else:
        return [], ["JSON 必须是数组或包含 rows 数组的对象"]

    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {idx} 行不是对象结构")
            continue
        normalized = {str(k or "").strip().lower(): v for k, v in item.items()}
        normalized["_row_index"] = idx
        rows.append(normalized)
    return rows, errors


def _normalize_import_row(
    row: dict[str, Any],
    fallback_crowd_type: Optional[str],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    crowd_type = str(
        row.get("crowd_type")
        or row.get("crowdtype")
        or row.get("type")
        or fallback_crowd_type
        or ""
    ).strip().upper()
    style_name = str(row.get("style_name") or row.get("style") or "").strip()
    positive_prompt = str(row.get("positive_prompt") or row.get("positive") or "").strip()
    negative_prompt = str(row.get("negative_prompt") or row.get("negative") or "").strip()

    if crowd_type not in CROWD_TYPES:
        return None, f"行 {row.get('_row_index', '?')}: crowd_type 无效"
    if not style_name:
        return None, f"行 {row.get('_row_index', '?')}: style_name 为空"
    if not positive_prompt:
        return None, f"行 {row.get('_row_index', '?')}: positive_prompt 为空"

    raw_ref_weight = row.get("reference_weight")
    ref_weight: int
    if str(raw_ref_weight or "").strip():
        try:
            ref_weight = _clamp_reference_weight(int(str(raw_ref_weight).strip()), default=80)
        except Exception:
            return None, f"行 {row.get('_row_index', '?')}: reference_weight 必须是数字"
    else:
        ref_weight = 80
    preferred_engine = _normalize_engine(
        str(row.get("preferred_engine") or "").strip() or None,
        fallback="seedream",
    )
    is_active = _normalize_active(row.get("is_active"), default=True)

    normalized = {
        "crowd_type": crowd_type,
        "style_name": style_name[:255],
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
        "reference_weight": ref_weight,
        "preferred_engine": preferred_engine,
        "is_active": is_active,
    }
    return normalized, None


def _run_prompt_apply_background(
    batch_id: str,
    crowd_type_ids: list[str],
    prompt_count: int,
    reference_image_id: str | None = None,
):
    db = SessionLocal()
    try:
        default_generate_engine = _normalize_engine(
            get_setting_value(db, "generate_engine", settings.IMAGE_GENERATION_ENGINE)
            or settings.IMAGE_GENERATION_ENGINE
        )
        prompt_prefix = (get_setting_value(db, "generate_prompt_prefix", "") or "").strip()
        prompt_suffix = (get_setting_value(db, "generate_prompt_suffix", "") or "").strip()

        base_images = db.query(BaseImage).filter(
            BaseImage.batch_id == batch_id,
            BaseImage.status == "completed",
        ).all()
        if not base_images:
            ps.fail(TASK_TYPE, batch_id, "没有已完成预处理的底图")
            return

        ref_image = None
        if reference_image_id:
            ref_image = next((img for img in base_images if img.id == reference_image_id), None)
        if ref_image is None:
            ref_image = base_images[0]

        selected_templates: dict[str, list[PromptTemplate]] = {}
        selected_style_names: dict[str, set[str]] = {}
        total_templates = 0

        for ct_id in crowd_type_ids:
            templates = db.query(PromptTemplate).filter(
                PromptTemplate.crowd_type == ct_id,
                PromptTemplate.is_active.is_(True),
            ).order_by(PromptTemplate.create_time.asc(), PromptTemplate.style_name.asc()).all()
            chosen = templates[: max(1, prompt_count)]
            selected_templates[ct_id] = chosen
            selected_style_names[ct_id] = {t.style_name for t in chosen}
            total_templates += len(chosen)

        if total_templates == 0:
            ps.fail(TASK_TYPE, batch_id, "当前人群类型没有可用提示词，请先在提示词词库新增或导入")
            return

        total_planned = len(base_images) * total_templates
        ps.init(
            TASK_TYPE,
            batch_id,
            total_planned,
            (
                f"开始按词库创建任务：{len(crowd_type_ids)} 类型 × {len(base_images)} 底图，"
                f"参考底图: {ref_image.filename}"
            ),
        )
        for ct_id in crowd_type_ids:
            ps.append_log(
                TASK_TYPE,
                batch_id,
                f"[OK] {CROWD_TYPES.get(ct_id, ct_id)} 载入模板 {len(selected_templates.get(ct_id, []))} 条",
            )

        # 清理当前批次中该人群不在“本次模板集合”的待处理任务
        stale_removed = 0
        for ct_id in crowd_type_ids:
            names = list(selected_style_names.get(ct_id, set()))
            if not names:
                continue
            stale_ids = [
                row[0]
                for row in db.query(GenerateTask.id).join(BaseImage).filter(
                    BaseImage.batch_id == batch_id,
                    GenerateTask.crowd_type == ct_id,
                    ~GenerateTask.style_name.in_(names),
                    GenerateTask.status.in_(["pending", "failed", "processing"]),
                ).all()
            ]
            if not stale_ids:
                continue
            stale_removed += len(stale_ids)
            db.query(GenerateTask).filter(GenerateTask.id.in_(stale_ids)).delete(
                synchronize_session=False
            )
        db.commit()
        if stale_removed > 0:
            ps.append_log(TASK_TYPE, batch_id, f"[CLEANUP] 已清理旧待处理任务 {stale_removed} 条")

        created_count = 0
        updated_count = 0
        skipped_completed = 0
        failed_count = 0
        done = 0

        for img in base_images:
            if ps.is_cancel_requested(TASK_TYPE, batch_id):
                ps.cancel(
                    TASK_TYPE,
                    batch_id,
                    created_count + updated_count,
                    failed_count,
                    (
                        f"任务创建已中断：新增 {created_count}，更新 {updated_count}，"
                        f"保留已完成 {skipped_completed}"
                    ),
                )
                return

            for ct_id in crowd_type_ids:
                for tmpl in selected_templates.get(ct_id, []):
                    done += 1
                    try:
                        merged_positive = " ".join(
                            token for token in [prompt_prefix, tmpl.positive_prompt, prompt_suffix] if token
                        )
                        task_prompt = _build_task_prompt(merged_positive, tmpl.style_name)
                        task_negative = _build_task_negative_prompt(tmpl.negative_prompt or "")
                        task_engine = _normalize_engine(
                            tmpl.preferred_engine,
                            fallback=default_generate_engine,
                        )

                        existing = db.query(GenerateTask).filter(
                            GenerateTask.base_image_id == img.id,
                            GenerateTask.crowd_type == ct_id,
                            GenerateTask.style_name == tmpl.style_name,
                        ).first()

                        if existing:
                            if existing.status == "completed":
                                skipped_completed += 1
                            else:
                                existing.prompt = task_prompt
                                existing.negative_prompt = task_negative
                                existing.ai_engine = task_engine
                                existing.status = "pending"
                                updated_count += 1
                        else:
                            db.add(GenerateTask(
                                base_image_id=img.id,
                                crowd_type=ct_id,
                                style_name=tmpl.style_name,
                                prompt=task_prompt,
                                negative_prompt=task_negative,
                                ai_engine=task_engine,
                                status="pending",
                            ))
                            created_count += 1
                    except Exception as e:
                        failed_count += 1
                        logger.error("创建生成任务失败: %s", e)
                        ps.append_log(TASK_TYPE, batch_id, f"[FAIL] {CROWD_TYPES.get(ct_id, ct_id)}-{tmpl.style_name}: {e}")

                    progress = int(done / max(1, total_planned) * 100)
                    ps.update(
                        TASK_TYPE,
                        batch_id,
                        progress=progress,
                        completed=created_count + updated_count,
                        failed=failed_count,
                    )

            db.commit()

        ps.finish(
            TASK_TYPE,
            batch_id,
            created_count + updated_count,
            failed_count,
            (
                f"词库任务创建完成：新增 {created_count}，更新 {updated_count}，"
                f"跳过已完成 {skipped_completed}，失败 {failed_count}"
            ),
        )
    except Exception as e:
        logger.exception("词库任务创建异常: %s", e)
        ps.fail(TASK_TYPE, batch_id, f"任务创建失败: {e}")
    finally:
        db.close()


@router.post("/generate", response_model=BaseResponse)
async def generate_prompts(request: PromptGenerateRequest, db: Session = Depends(get_db)):
    """
    按“已管理词库”创建生图任务（异步后台）
    - 当前仅支持单次一个单人类型
    - prompt_count 表示本次从词库取前 N 条模板
    """
    from app.models.database import Batch

    batch = db.query(Batch).filter(Batch.id == request.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    current = ps.get(TASK_TYPE, request.batch_id)
    if current.get("status") in ("running", "cancelling"):
        return BaseResponse(code=1, message="该批次提示词任务正在进行中")

    crowd_type_ids = list(dict.fromkeys(request.crowd_types or []))
    if not crowd_type_ids:
        return BaseResponse(code=1, message="请先选择人群类型")
    if len(crowd_type_ids) != 1:
        return BaseResponse(code=1, message="当前版本仅支持单次选择1个人群类型")
    if crowd_type_ids[0] not in SINGLE_TYPES:
        return BaseResponse(code=1, message="当前版本仅支持单人7类，组合人群暂未开放")

    ps.clear_cancel(TASK_TYPE, request.batch_id)
    t = threading.Thread(
        target=_run_prompt_apply_background,
        args=(request.batch_id, crowd_type_ids, request.prompt_count, request.reference_image_id),
        daemon=True,
    )
    t.start()

    return BaseResponse(
        code=0,
        message="已开始按词库创建任务",
        data={
            "batch_id": request.batch_id,
            "crowd_types_count": len(crowd_type_ids),
            "prompt_count": request.prompt_count,
            "reference_image_id": request.reference_image_id or "",
        },
    )


@router.post("/create", response_model=BaseResponse)
async def create_prompt(request: PromptCreateRequest, db: Session = Depends(get_db)):
    """新增单条提示词模板"""
    crowd_type = request.crowd_type.strip().upper()
    if crowd_type not in CROWD_TYPES:
        return BaseResponse(code=1, message=f"无效的人群类型: {crowd_type}")

    style_name = request.style_name.strip()
    if not style_name:
        return BaseResponse(code=1, message="style_name 不能为空")
    if not request.positive_prompt.strip():
        return BaseResponse(code=1, message="positive_prompt 不能为空")

    existing = db.query(PromptTemplate).filter(
        PromptTemplate.crowd_type == crowd_type,
        PromptTemplate.style_name == style_name,
    ).order_by(PromptTemplate.create_time.desc()).first()

    payload = {
        "positive_prompt": request.positive_prompt.strip(),
        "negative_prompt": (request.negative_prompt or "").strip(),
        "reference_weight": _clamp_reference_weight(request.reference_weight, default=80),
        "preferred_engine": _normalize_engine(request.preferred_engine, fallback="seedream"),
        "is_active": bool(request.is_active),
    }

    if existing:
        existing.positive_prompt = payload["positive_prompt"]
        existing.negative_prompt = payload["negative_prompt"]
        existing.reference_weight = payload["reference_weight"]
        existing.preferred_engine = payload["preferred_engine"]
        existing.is_active = payload["is_active"]
        db.commit()
        return BaseResponse(code=0, message="提示词已更新", data={"id": existing.id, "updated": True})

    tmpl = PromptTemplate(
        crowd_type=crowd_type,
        style_name=style_name[:255],
        **payload,
    )
    db.add(tmpl)
    db.commit()
    return BaseResponse(code=0, message="提示词已创建", data={"id": tmpl.id, "updated": False})


@router.post("/import", response_model=BaseResponse)
async def import_prompts(
    file: UploadFile = File(...),
    crowd_type: Optional[str] = Form(None),
    replace_current: bool = Form(False),
    db: Session = Depends(get_db),
):
    """
    导入提示词模板
    - 支持 CSV / JSON
    - replace_current=true 时会先停用导入覆盖范围内现有模板
    """
    raw = await file.read()
    if not raw:
        return BaseResponse(code=1, message="导入文件为空")

    try:
        content = raw.decode("utf-8-sig")
    except Exception:
        content = raw.decode("utf-8", errors="ignore")

    filename = (file.filename or "").lower()
    if filename.endswith(".json"):
        raw_rows, parse_errors = _parse_json_rows(content)
    elif filename.endswith(".csv"):
        raw_rows, parse_errors = _parse_csv_rows(content)
    else:
        # 默认按 CSV 解析，兼容用户手动粘贴导出的文件
        raw_rows, parse_errors = _parse_csv_rows(content)

    fallback_crowd = (crowd_type or "").strip().upper() or None
    if fallback_crowd and fallback_crowd not in CROWD_TYPES:
        return BaseResponse(code=1, message=f"参数 crowd_type 无效: {fallback_crowd}")

    normalized_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        normalized, err = _normalize_import_row(row, fallback_crowd)
        if err:
            parse_errors.append(err)
            continue
        normalized_rows.append(normalized)  # type: ignore[arg-type]

    if not normalized_rows:
        msg = "没有可导入数据"
        if parse_errors:
            msg += f"：{parse_errors[0]}"
        return BaseResponse(code=1, message=msg)

    affected_crowds = sorted({r["crowd_type"] for r in normalized_rows})
    if replace_current:
        db.query(PromptTemplate).filter(
            PromptTemplate.crowd_type.in_(affected_crowds),
            PromptTemplate.is_active.is_(True),
        ).update({PromptTemplate.is_active: False}, synchronize_session=False)
        db.commit()

    created_count = 0
    updated_count = 0

    for row in normalized_rows:
        existing = db.query(PromptTemplate).filter(
            PromptTemplate.crowd_type == row["crowd_type"],
            PromptTemplate.style_name == row["style_name"],
        ).order_by(PromptTemplate.create_time.desc()).first()

        if existing:
            existing.positive_prompt = row["positive_prompt"]
            existing.negative_prompt = row["negative_prompt"]
            existing.reference_weight = row["reference_weight"]
            existing.preferred_engine = row["preferred_engine"]
            existing.is_active = row["is_active"]
            updated_count += 1
        else:
            db.add(PromptTemplate(**row))
            created_count += 1

    db.commit()

    return BaseResponse(
        code=0,
        message=f"导入完成：新增 {created_count}，更新 {updated_count}",
        data={
            "created_count": created_count,
            "updated_count": updated_count,
            "error_count": len(parse_errors),
            "errors": parse_errors[:20],
            "affected_crowds": affected_crowds,
        },
    )


@router.get("/progress/{batch_id}", response_model=BaseResponse)
async def get_prompt_progress(batch_id: str):
    data = ps.get(TASK_TYPE, batch_id)
    return BaseResponse(code=0, data=data)


@router.post("/cancel/{batch_id}", response_model=BaseResponse)
async def cancel_prompt_generation(batch_id: str):
    if ps.request_cancel(TASK_TYPE, batch_id, "用户请求中断提示词任务"):
        return BaseResponse(code=0, message="已发送中断请求，任务将在安全点停止")
    return BaseResponse(code=1, message="当前没有运行中的提示词任务")


@router.get("/list", response_model=BaseResponse)
async def list_prompts(
    batch_id: Optional[str] = None,
    crowd_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(PromptTemplate).filter(PromptTemplate.is_active.is_(True))
    if crowd_type:
        query = query.filter(PromptTemplate.crowd_type == crowd_type)

    templates = query.order_by(
        PromptTemplate.crowd_type.asc(),
        PromptTemplate.create_time.asc(),
        PromptTemplate.style_name.asc(),
    ).all()

    result = []
    for t in templates:
        task_count = 0
        if batch_id:
            task_count = db.query(GenerateTask).filter(
                GenerateTask.crowd_type == t.crowd_type,
                GenerateTask.style_name == t.style_name,
            ).join(BaseImage).filter(BaseImage.batch_id == batch_id).count()
        result.append({
            "id": t.id,
            "crowd_type": t.crowd_type,
            "crowd_name": CROWD_TYPES.get(t.crowd_type, t.crowd_type),
            "style_name": t.style_name,
            "positive_prompt": t.positive_prompt,
            "negative_prompt": t.negative_prompt,
            "reference_weight": t.reference_weight,
            "preferred_engine": t.preferred_engine,
            "task_count": task_count,
        })

    return BaseResponse(code=0, data={"prompts": result, "total": len(result)})


@router.put("/edit/{prompt_id}", response_model=BaseResponse)
async def edit_prompt(
    prompt_id: str,
    positive_prompt: str = None,
    negative_prompt: str = None,
    style_name: str = None,
    reference_weight: int = None,
    preferred_engine: str = None,
    db: Session = Depends(get_db),
):
    template = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="提示词不存在")

    if positive_prompt is not None:
        template.positive_prompt = positive_prompt
    if negative_prompt is not None:
        template.negative_prompt = negative_prompt
    if style_name is not None:
        template.style_name = style_name[:255]
    if reference_weight is not None:
        template.reference_weight = _clamp_reference_weight(reference_weight)
    if preferred_engine is not None:
        template.preferred_engine = _normalize_engine(preferred_engine, fallback="seedream")

    db.commit()
    return BaseResponse(code=0, message="提示词已更新")


@router.delete("/delete/{prompt_id}", response_model=BaseResponse)
async def delete_prompt(prompt_id: str, db: Session = Depends(get_db)):
    template = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="提示词不存在")

    template.is_active = False
    db.commit()
    return BaseResponse(code=0, message="提示词已删除")


@router.delete("/delete-by-crowd/{crowd_type}", response_model=BaseResponse)
async def delete_prompts_by_crowd(crowd_type: str, db: Session = Depends(get_db)):
    if crowd_type not in CROWD_TYPES:
        raise HTTPException(status_code=400, detail=f"无效的人群类型: {crowd_type}")

    templates = db.query(PromptTemplate).filter(
        PromptTemplate.crowd_type == crowd_type,
        PromptTemplate.is_active.is_(True),
    ).all()
    if not templates:
        return BaseResponse(code=0, message="当前人群没有可删除的提示词", data={"deleted_count": 0})

    for tmpl in templates:
        tmpl.is_active = False
    db.commit()

    return BaseResponse(
        code=0,
        message=f"已删除 {len(templates)} 条提示词",
        data={"deleted_count": len(templates)},
    )
