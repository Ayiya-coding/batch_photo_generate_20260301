"""
宽脸图生成API路由
- 对选用库中指定模板图生成宽脸版本
- 使用 API易平台 (Nano Banana Pro / SeedDream 4.5)
- 后台异步执行 + 进度轮询
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime
import asyncio
import threading
import logging

from app.core.database import get_db, SessionLocal
from app.core.config import settings
from app.core.settings_resolver import get_setting_value
from app.schemas.common import WideFaceGenerateRequest, WideFaceReviewRequest, BaseResponse
from app.models.database import TemplateImage
from app.services import progress_store as ps

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_TYPE = "wideface"
TASK_KEY = "current"


def _normalize_watermark_engine(engine_name: str) -> str:
    engine = (engine_name or "auto").strip().lower()
    alias = {
        "volcengine": "volc",
        "volcano": "volc",
        "local": "iopaint",
    }
    engine = alias.get(engine, engine)
    if engine in ("auto", "iopaint", "volc", "opencv"):
        return engine
    return "auto"


def _run_wideface_background(template_ids: list[str], engine: str):
    """后台线程入口"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_wideface_generate(template_ids, engine))
    finally:
        loop.close()


async def _async_wideface_generate(template_ids: list[str], engine: str):
    """异步宽脸图生成核心逻辑"""
    db = SessionLocal()
    generator = None
    try:
        from app.services.image_generator import ConcurrentImageGenerator

        api_key = get_setting_value(db, "apiyi_api_key", "") or settings.APIYI_API_KEY
        wideface_prompt = get_setting_value(
            db, "wideface_prompt", ""
        ) or settings.WIDEFACE_SYSTEM_PROMPT
        disable_generation_watermark = (
            get_setting_value(db, "disable_generation_watermark", "1").strip() != "0"
        )
        strict_no_watermark = (
            get_setting_value(db, "strict_no_watermark", "1").strip() != "0"
        )
        watermark_engine = _normalize_watermark_engine(
            get_setting_value(db, "watermark_engine", "auto")
        )
        volc_access_key_id = get_setting_value(db, "volc_access_key_id", "") or settings.VOLC_ACCESS_KEY_ID
        volc_secret_access_key = (
            get_setting_value(db, "volc_secret_access_key", "") or settings.VOLC_SECRET_ACCESS_KEY
        )

        templates = db.query(TemplateImage).filter(
            TemplateImage.id.in_(template_ids),
        ).all()

        if not templates:
            ps.finish(TASK_TYPE, TASK_KEY, 0, 0, "没有找到指定的模板图")
            return

        total = len(templates)
        reason_stats: dict[str, int] = {}
        ps.init(
            TASK_TYPE,
            TASK_KEY,
            total,
            f"开始宽脸图生成: {total} 张, 引擎={engine}",
            reason_stats=reason_stats,
        )
        quota_alerted = False

        generator = ConcurrentImageGenerator(
            api_key=api_key,
            disable_watermark=disable_generation_watermark,
            strict_no_watermark=strict_no_watermark,
            watermark_engine=watermark_engine,
            iopaint_url=settings.IOPAINT_URL,
            volc_access_key_id=volc_access_key_id,
            volc_secret_access_key=volc_secret_access_key,
            volc_region=settings.VOLC_REGION,
            volc_service=settings.VOLC_SERVICE,
        )
        completed = 0
        failed = 0

        sem = asyncio.Semaphore(5)  # 宽脸图并发较低

        async def process_one(tmpl_id: str):
            nonlocal completed, failed, quota_alerted
            if ps.is_cancel_requested(TASK_TYPE, TASK_KEY):
                return

            async with sem:
                if ps.is_cancel_requested(TASK_TYPE, TASK_KEY):
                    return
                task_db = SessionLocal()
                try:
                    tmpl = task_db.query(TemplateImage).filter(
                        TemplateImage.id == tmpl_id
                    ).first()
                    if not tmpl or not tmpl.original_path:
                        failed += 1
                        _update_progress(total, completed, failed, f"[FAIL] 模板不存在: {tmpl_id[:8]}")
                        return

                    tmpl.wide_face_status = "processing"
                    task_db.commit()

                    out_filename = f"wideface_{tmpl.id}.jpg"
                    out_path = str(settings.GENERATED_DIR / out_filename)

                    prompt = f"{wideface_prompt}, based on the original photo, generate a wider face version"

                    success, fail_detail, fail_codes = await generator.generate_single_with_retry_detail(
                        engine=engine,
                        prompt=prompt,
                        negative_prompt="distorted, deformed, ugly, blurry",
                        reference_image_path=tmpl.original_path,
                        reference_weight=90,
                        output_path=out_path,
                    )

                    if success:
                        tmpl.wide_face_path = out_path
                        tmpl.wide_face_status = "completed"
                        completed += 1
                        _update_progress(total, completed, failed,
                                         f"[OK] {tmpl.crowd_type}-{tmpl.style_name}")
                    else:
                        tmpl.wide_face_status = "failed"
                        failed += 1
                        if fail_codes:
                            for code in fail_codes:
                                reason_stats[code] = reason_stats.get(code, 0) + 1
                        _update_progress(total, completed, failed,
                                         f"[FAIL] {tmpl.crowd_type}-{tmpl.style_name} | {fail_detail or '未知失败'}",
                                         reason_stats=reason_stats)
                        if ("insufficient_user_quota" in (fail_codes or [])) and (not quota_alerted):
                            _update_progress(
                                total,
                                completed,
                                failed,
                                "[ALERT] 检测到上游额度不足（insufficient_user_quota）：请充值 API易 或更换可用 Key 后再重试。",
                                reason_stats=reason_stats,
                            )
                            quota_alerted = True

                    task_db.commit()
                finally:
                    task_db.close()

        await asyncio.gather(*[process_one(tid) for tid in template_ids])

        if ps.is_cancel_requested(TASK_TYPE, TASK_KEY):
            reset_count = db.query(TemplateImage).filter(
                TemplateImage.id.in_(template_ids),
                TemplateImage.wide_face_status == "processing",
            ).update({TemplateImage.wide_face_status: "none"}, synchronize_session=False)
            if reset_count > 0:
                db.commit()

            ps.cancel(
                TASK_TYPE,
                TASK_KEY,
                completed,
                failed,
                f"宽脸图生成已中断：已完成 {completed}，失败 {failed}，剩余任务已恢复待生成",
                reason_stats=reason_stats,
            )
            return

        summary = f"宽脸图生成完成！成功 {completed} 张，失败 {failed} 张"
        if reason_stats:
            detail = ", ".join(
                [f"{k}={v}" for k, v in sorted(reason_stats.items(), key=lambda x: (-x[1], x[0]))]
            )
            summary += f" | 失败原因统计: {detail}"
        ps.finish(
            TASK_TYPE,
            TASK_KEY,
            completed,
            failed,
            summary,
            reason_stats=reason_stats,
        )

    except Exception as e:
        logger.error(f"宽脸图生成失败: {e}")
        ps.fail(TASK_TYPE, TASK_KEY, f"宽脸图生成出错: {str(e)}")
    finally:
        if generator:
            try:
                await generator.close()
            except Exception:
                pass
        db.close()


def _update_progress(
    total: int,
    completed: int,
    failed: int,
    log_msg: str,
    reason_stats: dict | None = None,
):
    done = completed + failed
    progress = int(done / total * 100) if total > 0 else 0
    current = ps.get(TASK_TYPE, TASK_KEY)
    current.update({
        "progress": progress,
        "completed": completed,
        "failed": failed,
    })
    if reason_stats is not None:
        current["reason_stats"] = reason_stats
    logs = current.get("logs", [])
    logs.append(log_msg)
    current["logs"] = logs
    ps.set(TASK_TYPE, TASK_KEY, current)


@router.post("/generate", response_model=BaseResponse)
async def generate_wideface(
    request: WideFaceGenerateRequest, db: Session = Depends(get_db)
):
    """批量生成宽脸图"""
    current = ps.get(TASK_TYPE, TASK_KEY)
    if current.get("status") in ("running", "cancelling"):
        return BaseResponse(code=1, message="宽脸图生成任务正在进行中")

    # 验证模板存在
    templates = db.query(TemplateImage).filter(
        TemplateImage.id.in_(request.template_ids),
    ).all()

    if not templates:
        return BaseResponse(code=1, message="未找到指定的模板图")

    engine = request.engine or get_setting_value(
        db, "wideface_engine", ""
    ) or settings.WIDEFACE_GENERATION_ENGINE

    t = threading.Thread(
        target=_run_wideface_background,
        args=(request.template_ids, engine),
        daemon=True,
    )
    ps.clear_cancel(TASK_TYPE, TASK_KEY)
    t.start()

    return BaseResponse(code=0, message="宽脸图生成已启动", data={
        "count": len(templates),
        "engine": engine,
    })


@router.get("/progress", response_model=BaseResponse)
async def get_wideface_progress():
    """获取宽脸图生成进度"""
    data = ps.get(TASK_TYPE, TASK_KEY)
    return BaseResponse(code=0, data=data)


@router.post("/cancel", response_model=BaseResponse)
async def cancel_wideface():
    """中断宽脸图生成任务"""
    if ps.request_cancel(TASK_TYPE, TASK_KEY, "用户请求中断宽脸图生成"):
        return BaseResponse(code=0, message="已发送中断请求，任务将在安全点停止")
    return BaseResponse(code=1, message="当前没有运行中的宽脸图任务")


@router.post("/review", response_model=BaseResponse)
async def review_wideface(
    request: WideFaceReviewRequest, db: Session = Depends(get_db)
):
    """宽脸图审核（通过/重生）"""
    tmpl = db.query(TemplateImage).filter(
        TemplateImage.id == request.template_id
    ).first()

    if not tmpl:
        raise HTTPException(status_code=404, detail="模板图不存在")

    if request.status == "pass":
        # 审核通过，保持当前宽脸图
        tmpl.wide_face_status = "completed"
        db.commit()
        return BaseResponse(code=0, message="宽脸图审核通过")

    elif request.status == "regenerate":
        # 需要重新生成
        tmpl.wide_face_status = "none"
        tmpl.wide_face_path = None
        db.commit()
        return BaseResponse(code=0, message="已标记为需要重新生成")

    else:
        return BaseResponse(code=1, message="无效的审核状态，请使用 pass 或 regenerate")
