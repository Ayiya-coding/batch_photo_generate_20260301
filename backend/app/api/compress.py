"""
画质压缩API路由
- 对选用库全部模板图进行画质压缩
- 二分查找最佳质量值，画质优先
- 后台异步执行 + 进度轮询
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import threading
import logging

from app.core.database import get_db, SessionLocal
from app.core.config import settings
from app.core.settings_resolver import get_setting_value
from app.schemas.common import CompressRequest, BaseResponse
from app.models.database import TemplateImage
from app.services import progress_store as ps

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_TYPE = "compress"
TASK_KEY = "current"


def _run_compress_background(
    target_size_kb: int, min_quality: int, max_quality: int
):
    """后台线程入口"""
    db = SessionLocal()
    try:
        _sync_compress(db, target_size_kb, min_quality, max_quality)
    except Exception as e:
        logger.error(f"压缩任务异常: {e}")
        ps.fail(TASK_TYPE, TASK_KEY, f"压缩出错: {str(e)}")
    finally:
        db.close()


def _sync_compress(
    db: Session, target_size_kb: int, min_quality: int, max_quality: int
):
    """同步压缩核心逻辑"""
    from app.services.image_compressor import compress_image

    # 查询所有选用状态且未压缩的模板图
    templates = db.query(TemplateImage).filter(
        TemplateImage.final_status == "selected",
        TemplateImage.compress_status.in_(["none", "failed"]),
    ).all()

    if not templates:
        ps.finish(TASK_TYPE, TASK_KEY, 0, 0, "没有需要压缩的图片")
        return

    total = len(templates)
    ps.init(TASK_TYPE, TASK_KEY, total, f"开始压缩: {total} 张图片, 目标 {target_size_kb}KB")

    completed = 0
    failed = 0

    for tmpl in templates:
        tmpl.compress_status = "processing"
        db.commit()

        # 压缩原图
        src_path = tmpl.original_path
        if not src_path:
            tmpl.compress_status = "failed"
            db.commit()
            failed += 1
            _update_progress(total, completed, failed, f"[FAIL] 无源文件: {tmpl.id[:8]}")
            continue

        out_filename = f"compressed_{tmpl.id}.jpg"
        out_path = str(settings.COMPRESSED_DIR / out_filename)

        success = compress_image(
            input_path=src_path,
            output_path=out_path,
            target_size_kb=target_size_kb,
            min_quality=min_quality,
            max_quality=max_quality,
        )

        if success:
            tmpl.compressed_path = out_path
            tmpl.compress_status = "completed"
            tmpl.compress_time = datetime.utcnow()
            completed += 1
            _update_progress(total, completed, failed, f"[OK] {tmpl.crowd_type}-{tmpl.style_name}")
        else:
            tmpl.compress_status = "failed"
            failed += 1
            _update_progress(total, completed, failed, f"[FAIL] {tmpl.crowd_type}-{tmpl.style_name}")

        db.commit()

        # 压缩宽脸图（如果有）
        if tmpl.wide_face_path:
            wf_out_filename = f"compressed_wf_{tmpl.id}.jpg"
            wf_out_path = str(settings.COMPRESSED_DIR / wf_out_filename)
            wf_success = compress_image(
                input_path=tmpl.wide_face_path,
                output_path=wf_out_path,
                target_size_kb=target_size_kb,
                min_quality=min_quality,
                max_quality=max_quality,
            )
            if wf_success:
                tmpl.compressed_wide_face_path = wf_out_path
                db.commit()

    ps.finish(TASK_TYPE, TASK_KEY, completed, failed,
              f"压缩完成！成功 {completed} 张，失败 {failed} 张")


def _update_progress(total: int, completed: int, failed: int, log_msg: str):
    done = completed + failed
    progress = int(done / total * 100) if total > 0 else 0
    current = ps.get(TASK_TYPE, TASK_KEY)
    current.update({
        "progress": progress,
        "completed": completed,
        "failed": failed,
    })
    logs = current.get("logs", [])
    logs.append(log_msg)
    current["logs"] = logs
    ps.set(TASK_TYPE, TASK_KEY, current)


@router.post("/start", response_model=BaseResponse)
async def start_compress(request: CompressRequest, db: Session = Depends(get_db)):
    """一键压缩选用库全部图片（二分查找最佳质量值，画质优先）"""
    if ps.is_running(TASK_TYPE, TASK_KEY):
        return BaseResponse(code=1, message="压缩任务正在进行中")

    compress_enabled = str(get_setting_value(db, "compress_enabled", "1")).strip() == "1"
    if not compress_enabled:
        return BaseResponse(code=1, message="画质压缩功能已关闭，请在系统设置中开启后重试")

    # 读取配置
    target_kb = request.target_size_kb or int(get_setting_value(db, "compress_target_size", "500"))
    min_q = request.min_quality or int(get_setting_value(db, "compress_min_quality", "60"))
    max_q = request.max_quality or int(get_setting_value(db, "compress_max_quality", "95"))

    t = threading.Thread(
        target=_run_compress_background,
        args=(target_kb, min_q, max_q),
        daemon=True,
    )
    t.start()

    return BaseResponse(code=0, message="压缩任务已启动", data={
        "target_size_kb": target_kb,
        "min_quality": min_q,
        "max_quality": max_q,
    })


@router.get("/progress", response_model=BaseResponse)
async def get_compress_progress():
    """获取压缩进度"""
    data = ps.get(TASK_TYPE, TASK_KEY)
    return BaseResponse(code=0, data=data)


@router.post("/retry/{image_id}", response_model=BaseResponse)
async def retry_compress(image_id: str, db: Session = Depends(get_db)):
    """重试失败的压缩任务"""
    tmpl = db.query(TemplateImage).filter(TemplateImage.id == image_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板图不存在")
    if tmpl.compress_status != "failed":
        return BaseResponse(code=1, message="该图片不是失败状态")

    tmpl.compress_status = "none"
    db.commit()
    return BaseResponse(code=0, message="已重置压缩状态，请重新启动压缩")
