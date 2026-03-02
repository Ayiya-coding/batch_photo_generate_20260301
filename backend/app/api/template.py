"""
模板管理API路由
- 模板图列表（三库视图：选用库/待修改库/回收站）
- 模板图上传/替换
- 模板图库间移动
- 模板图删除
- 模板图统计
- 模板图文件服务
"""
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import CROWD_TYPES
from app.core.database import get_db
from app.core.security import sanitize_filename
from app.models.database import BaseImage, Batch, GenerateTask, TemplateImage
from app.schemas.common import TemplateMoveRequest, BaseResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# 前端人群名称兼容映射（处理括号/命名差异）
_CROWD_NAME_TO_ID = {
    "幼女": "C01",
    "少女": "C02",
    "熟女": "C03",
    "奶奶": "C04",
    "幼男": "C05",
    "少男": "C06",
    "大叔": "C07",
    "情侣": "C08",
    "闺蜜": "C09",
    "兄弟": "C10",
    "异性伙伴": "C11",
    "母子(少年)": "C12",
    "母子(青年)": "C13",
    "母女(少年)": "C14",
    "母女(青年)": "C15",
    "父子(少年)": "C16",
    "父子(青年)": "C17",
    "父女(少年)": "C18",
    "父女(青年)": "C19",
    # 兼容当前前端“幼年”命名
    "母子(幼年)": "C13",
    "母女(幼年)": "C15",
    "父子(幼年)": "C17",
    "父女(幼年)": "C19",
    # 兼容后端历史无括号命名
    "母子少年": "C12",
    "母子青年": "C13",
    "母女少年": "C14",
    "母女青年": "C15",
    "父子少年": "C16",
    "父子青年": "C17",
    "父女少年": "C18",
    "父女青年": "C19",
}


def _normalize_crowd_name(name: str) -> str:
    return name.strip().replace("（", "(").replace("）", ")").replace(" ", "")


def _resolve_crowd_type(crowd: str) -> str:
    """支持 crowd_type=CN 名称或 Cxx ID。"""
    value = (crowd or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="缺少人群类型")

    if value in CROWD_TYPES:
        return value

    normalized = _normalize_crowd_name(value)
    for raw_name, cid in _CROWD_NAME_TO_ID.items():
        if _normalize_crowd_name(raw_name) == normalized:
            return cid

    raise HTTPException(status_code=400, detail=f"无效人群类型: {crowd}")


async def _read_upload_file(upload_file: UploadFile) -> tuple[str, str, bytes]:
    """统一校验上传文件并返回(安全文件名, 扩展名, 字节内容)。"""
    if not upload_file.filename:
        raise ValueError("文件名为空")

    safe_name = sanitize_filename(upload_file.filename)
    ext = Path(safe_name).suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}")

    content = await upload_file.read()
    if not content:
        raise ValueError("空文件")

    if len(content) > settings.MAX_UPLOAD_SIZE:
        max_mb = settings.MAX_UPLOAD_SIZE // 1024 // 1024
        raise ValueError(f"文件超过 {max_mb}MB 限制")

    return safe_name, ext, content


def _to_template_item(t: TemplateImage) -> dict:
    return {
        "id": t.id,
        "generate_task_id": t.generate_task_id,
        "crowd_type": t.crowd_type,
        "crowd_name": CROWD_TYPES.get(t.crowd_type, t.crowd_type),
        "style_name": t.style_name,
        "original_path": t.original_path,
        "wide_face_path": t.wide_face_path,
        "wide_face_status": t.wide_face_status,
        "compress_status": t.compress_status,
        "compressed_path": t.compressed_path,
        "final_status": t.final_status,
        "create_time": t.create_time.isoformat() if t.create_time else "",
    }


@router.post("/upload", response_model=BaseResponse)
async def upload_templates(
    files: List[UploadFile] = File(...),
    crowd_type: str = Form(..., description="支持 Cxx 或中文名称"),
    db: Session = Depends(get_db),
):
    """模板库上传（持久化入库，供宽脸图流程使用）。"""
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一张图片")
    if len(files) > settings.MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {settings.MAX_BATCH_SIZE} 张图片")

    crowd_type_id = _resolve_crowd_type(crowd_type)
    crowd_name = CROWD_TYPES.get(crowd_type_id, crowd_type_id)

    batch = Batch(
        name=f"模板管理上传_{datetime.utcnow():%Y%m%d_%H%M%S}",
        description=f"模板管理手工上传 - {crowd_name}",
        status="completed",
        total_images=0,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    created_items: List[dict] = []
    failed_files: List[dict] = []

    for idx, upload_file in enumerate(files):
        save_path: Optional[Path] = None
        try:
            safe_name, ext, content = await _read_upload_file(upload_file)
            save_path = settings.SELECTED_DIR / f"tpl_{uuid.uuid4()}{ext}"
            save_path.write_bytes(content)

            style_name = Path(safe_name).stem[:255] or f"手动模板_{idx + 1}"

            base_image = BaseImage(
                batch_id=batch.id,
                filename=safe_name,
                original_path=str(save_path),
                status="completed",
                preprocess_mode="crop",
                watermark_removed=True,
            )
            db.add(base_image)
            db.flush()

            generate_task = GenerateTask(
                base_image_id=base_image.id,
                crowd_type=crowd_type_id,
                style_name=style_name,
                ai_engine="manual_upload",
                result_path=str(save_path),
                status="completed",
                review_status="selected",
                complete_time=datetime.utcnow(),
            )
            db.add(generate_task)
            db.flush()

            template = TemplateImage(
                generate_task_id=generate_task.id,
                crowd_type=crowd_type_id,
                style_name=style_name,
                original_path=str(save_path),
                final_status="selected",
            )
            db.add(template)
            db.commit()
            db.refresh(template)
            created_items.append(_to_template_item(template))
        except ValueError as e:
            db.rollback()
            if save_path and save_path.exists():
                save_path.unlink(missing_ok=True)
            failed_files.append({
                "name": upload_file.filename or "",
                "reason": str(e),
            })
        except Exception as e:
            db.rollback()
            if save_path and save_path.exists():
                save_path.unlink(missing_ok=True)
            logger.exception("模板上传失败: %s", upload_file.filename)
            failed_files.append({
                "name": upload_file.filename or "",
                "reason": str(e),
            })

    batch.total_images = len(created_items)
    db.commit()

    return BaseResponse(
        code=0,
        message=f"上传完成，成功 {len(created_items)} 张，失败 {len(failed_files)} 张",
        data={
            "items": created_items,
            "uploaded_count": len(created_items),
            "failed_count": len(failed_files),
            "failed_files": failed_files,
        },
    )


@router.post("/replace/{template_id}", response_model=BaseResponse)
async def replace_template(
    template_id: str,
    file: UploadFile = File(...),
    is_wide_face: bool = Form(False),
    db: Session = Depends(get_db),
):
    """替换模板原图或宽脸图（持久化）。"""
    tpl = db.query(TemplateImage).filter(TemplateImage.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板图不存在")

    safe_name, ext, content = await _read_upload_file(file)
    replace_tag = "wide" if is_wide_face else "origin"
    save_path = settings.SELECTED_DIR / f"replace_{template_id}_{replace_tag}_{uuid.uuid4().hex[:8]}{ext}"
    save_path.write_bytes(content)

    try:
        if is_wide_face:
            tpl.wide_face_path = str(save_path)
            tpl.wide_face_status = "completed"
            tpl.compressed_wide_face_path = None
        else:
            old_original = tpl.original_path
            tpl.original_path = str(save_path)
            tpl.replaced_from = old_original

            # 原图变更后，宽脸图需要重做
            tpl.wide_face_path = None
            tpl.wide_face_status = "none"
            tpl.compressed_wide_face_path = None

            # 清空压缩结果，避免引用旧图
            tpl.compress_status = "none"
            tpl.compressed_path = None

            task = db.query(GenerateTask).filter(GenerateTask.id == tpl.generate_task_id).first()
            if task:
                task.result_path = str(save_path)
                task.status = "completed"
                task.review_status = "selected"

        # 风格名改为当前文件名（便于追溯）
        style_name = Path(safe_name).stem[:255]
        if style_name:
            tpl.style_name = style_name

        db.commit()
    except Exception:
        db.rollback()
        save_path.unlink(missing_ok=True)
        raise

    return BaseResponse(
        code=0,
        message="模板替换成功",
        data={"item": _to_template_item(tpl)},
    )


@router.get("/list", response_model=BaseResponse)
async def list_templates(
    crowd_type: Optional[str] = Query(None),
    batch_id: Optional[str] = Query(None, description="按批次筛选（可选）"),
    final_status: Optional[str] = Query("selected", description="selected/pending_modification/trash"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """获取模板图列表（按人群类型+库筛选）"""
    query = db.query(TemplateImage)

    if crowd_type:
        query = query.filter(TemplateImage.crowd_type == crowd_type)
    if batch_id:
        query = (
            query.join(GenerateTask, TemplateImage.generate_task_id == GenerateTask.id)
            .join(BaseImage, GenerateTask.base_image_id == BaseImage.id)
            .filter(BaseImage.batch_id == batch_id)
        )
    if final_status:
        query = query.filter(TemplateImage.final_status == final_status)

    total = query.count()
    templates = (
        query.order_by(TemplateImage.crowd_type, TemplateImage.style_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [_to_template_item(t) for t in templates]

    return BaseResponse(code=0, data={
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.post("/move", response_model=BaseResponse)
async def move_template(request: TemplateMoveRequest, db: Session = Depends(get_db)):
    """移动模板图到指定库"""
    valid_targets = ("selected", "pending_modification", "trash")
    if request.target not in valid_targets:
        raise HTTPException(status_code=400, detail=f"无效目标库: {request.target}")

    tpl = db.query(TemplateImage).filter(TemplateImage.id == request.template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板图不存在")

    tpl.final_status = request.target

    # 同步更新 GenerateTask 的 review_status
    if tpl.generate_task_id:
        task = db.query(GenerateTask).filter(GenerateTask.id == tpl.generate_task_id).first()
        if task:
            status_map = {
                "selected": "selected",
                "pending_modification": "pending_modification",
                "trash": "not_selected",
            }
            task.review_status = status_map[request.target]

    db.commit()

    target_names = {"selected": "选用库", "pending_modification": "待修改库", "trash": "回收站"}
    return BaseResponse(
        code=0,
        message=f"已移动到「{target_names[request.target]}」",
    )


@router.post("/batch-move", response_model=BaseResponse)
async def batch_move_templates(
    template_ids: List[str],
    target: str,
    db: Session = Depends(get_db),
):
    """批量移动模板图"""
    valid_targets = ("selected", "pending_modification", "trash")
    if target not in valid_targets:
        raise HTTPException(status_code=400, detail=f"无效目标库: {target}")

    status_map = {
        "selected": "selected",
        "pending_modification": "pending_modification",
        "trash": "not_selected",
    }

    updated = 0
    for tid in template_ids:
        tpl = db.query(TemplateImage).filter(TemplateImage.id == tid).first()
        if not tpl:
            continue
        tpl.final_status = target

        if tpl.generate_task_id:
            task = db.query(GenerateTask).filter(GenerateTask.id == tpl.generate_task_id).first()
            if task:
                task.review_status = status_map[target]

        updated += 1

    db.commit()

    target_names = {"selected": "选用库", "pending_modification": "待修改库", "trash": "回收站"}
    return BaseResponse(
        code=0,
        message=f"已批量移动 {updated} 张到「{target_names[target]}」",
        data={"updated_count": updated},
    )


@router.delete("/delete/{template_id}", response_model=BaseResponse)
async def delete_template(template_id: str, db: Session = Depends(get_db)):
    """永久删除模板图（仅回收站中的可删除）"""
    tpl = db.query(TemplateImage).filter(TemplateImage.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板图不存在")

    if tpl.final_status != "trash":
        raise HTTPException(status_code=400, detail="只能删除回收站中的模板图")

    db.delete(tpl)
    db.commit()

    return BaseResponse(code=0, message="已永久删除")


@router.get("/stats", response_model=BaseResponse)
async def template_stats(
    crowd_type: Optional[str] = Query(None),
    batch_id: Optional[str] = Query(None, description="按批次筛选（可选）"),
    db: Session = Depends(get_db),
):
    """获取模板图统计数据"""
    base_query = db.query(TemplateImage)
    if batch_id:
        base_query = (
            base_query.join(GenerateTask, TemplateImage.generate_task_id == GenerateTask.id)
            .join(BaseImage, GenerateTask.base_image_id == BaseImage.id)
            .filter(BaseImage.batch_id == batch_id)
        )
    if crowd_type:
        base_query = base_query.filter(TemplateImage.crowd_type == crowd_type)

    total = base_query.count()
    selected = base_query.filter(TemplateImage.final_status == "selected").count()
    pending_mod = base_query.filter(TemplateImage.final_status == "pending_modification").count()
    trash = base_query.filter(TemplateImage.final_status == "trash").count()

    # 按人群类型分组统计（仅选用库）
    crowd_stats = {}
    for ct_id, ct_name in CROWD_TYPES.items():
        per_ct_query = db.query(TemplateImage)
        if batch_id:
            per_ct_query = (
                per_ct_query.join(GenerateTask, TemplateImage.generate_task_id == GenerateTask.id)
                .join(BaseImage, GenerateTask.base_image_id == BaseImage.id)
                .filter(BaseImage.batch_id == batch_id)
            )
        count = per_ct_query.filter(
            TemplateImage.crowd_type == ct_id,
            TemplateImage.final_status == "selected",
        ).count()
        if count > 0:
            crowd_stats[ct_id] = {"name": ct_name, "count": count}

    return BaseResponse(code=0, data={
        "total": total,
        "selected": selected,
        "pending_modification": pending_mod,
        "trash": trash,
        "crowd_stats": crowd_stats,
    })


@router.get("/image/{template_id}")
async def serve_template_image(template_id: str, db: Session = Depends(get_db)):
    """提供模板图文件访问"""
    tpl = db.query(TemplateImage).filter(TemplateImage.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板图不存在")

    # 优先返回压缩版，其次原图
    image_path = tpl.compressed_path or tpl.original_path
    if not image_path:
        raise HTTPException(status_code=404, detail="图片路径为空")

    file_path = Path(image_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="图片文件未找到")

    return FileResponse(
        str(file_path),
        media_type="image/jpeg",
        filename=f"{tpl.crowd_type}_{tpl.style_name}.jpg",
    )
