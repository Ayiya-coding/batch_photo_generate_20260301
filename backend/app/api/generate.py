"""
批量生图API路由
- 异步后台批量生图 + 进度轮询
- 智能并发控制
- 失败重试
- 完成后自动进入审核队列
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pathlib import Path
from datetime import datetime
import asyncio
import threading
import logging

from app.core.database import get_db, SessionLocal
from app.core.config import settings
from app.core.settings_resolver import get_setting_value
from app.core.constants import CROWD_TYPES
from app.schemas.common import GenerateRequest, BaseResponse
from app.models.database import BaseImage, GenerateTask, TemplateImage, Batch
from app.services import progress_store as ps

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_TYPE = "generate"


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


def _run_generate_background(batch_id: str, engine: str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_batch_generate(batch_id, engine))
    finally:
        loop.close()


async def _async_batch_generate(batch_id: str, engine: str):
    """异步批量生图核心逻辑"""
    db = SessionLocal()
    generator = None
    try:
        from app.services.image_generator import ConcurrentImageGenerator

        api_key = get_setting_value(db, "apiyi_api_key", "") or settings.APIYI_API_KEY
        seedream_model = get_setting_value(db, "generate_model_version", "seedream-4-5-251128")
        nanobanana_model = get_setting_value(db, "nanobanana_model_version", "nano-banana-pro")
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

        # 查询所有待生成任务
        tasks = db.query(GenerateTask).join(BaseImage).filter(
            BaseImage.batch_id == batch_id,
            GenerateTask.status.in_(["pending", "failed"]),
        ).all()

        if not tasks:
            ps.finish(TASK_TYPE, batch_id, 0, 0, "没有待生成的任务", per_image={})
            return

        total = len(tasks)

        # 按底图分组统计
        image_task_map = {}
        for t in tasks:
            if t.base_image_id not in image_task_map:
                img = db.query(BaseImage).filter(BaseImage.id == t.base_image_id).first()
                image_task_map[t.base_image_id] = {
                    "filename": img.filename if img else "unknown",
                    "total": 0, "completed": 0, "failed": 0,
                }
            image_task_map[t.base_image_id]["total"] += 1

        per_image = {
            img_id: {**info, "progress": 0}
            for img_id, info in image_task_map.items()
        }
        reason_stats: dict[str, int] = {}
        quota_alerted = False

        ps.init(TASK_TYPE, batch_id, total,
                f"开始批量生图: {total} 个任务, 引擎={engine}",
                per_image=per_image,
                reason_stats=reason_stats)

        generator = ConcurrentImageGenerator(
            api_key=api_key,
            seedream_model=seedream_model,
            nanobanana_model=nanobanana_model,
            disable_watermark=disable_generation_watermark,
            strict_no_watermark=strict_no_watermark,
            watermark_engine=watermark_engine,
            iopaint_url=settings.IOPAINT_URL,
            volc_access_key_id=volc_access_key_id,
            volc_secret_access_key=volc_secret_access_key,
            volc_region=settings.VOLC_REGION,
            volc_service=settings.VOLC_SERVICE,
        )
        completed_count = 0
        failed_count = 0

        # 并发生成
        sem = asyncio.Semaphore(10)

        async def process_task(task_obj):
            nonlocal completed_count, failed_count, quota_alerted
            if ps.is_cancel_requested(TASK_TYPE, batch_id):
                return

            async with sem:
                if ps.is_cancel_requested(TASK_TYPE, batch_id):
                    return

                task_id = task_obj.id
                img_id = task_obj.base_image_id

                # 获取底图路径
                task_db = SessionLocal()
                success = False
                try:
                    t = task_db.query(GenerateTask).filter(GenerateTask.id == task_id).first()
                    img = task_db.query(BaseImage).filter(BaseImage.id == img_id).first()
                    if not t or not img:
                        return

                    if t.status == "completed":
                        return

                    t.status = "processing"
                    task_db.commit()

                    if ps.is_cancel_requested(TASK_TYPE, batch_id):
                        t.status = "pending"
                        task_db.commit()
                        return

                    ref_path = img.processed_path or img.original_path
                    output_filename = f"{task_id}_{t.crowd_type}_{t.style_name}.jpg"
                    output_path = str(settings.GENERATED_DIR / output_filename)

                    task_engine = engine or t.ai_engine or settings.IMAGE_GENERATION_ENGINE

                    success, fail_detail, fail_codes = await generator.generate_single_with_retry_detail(
                        engine=task_engine,
                        prompt=t.prompt or "",
                        negative_prompt=t.negative_prompt or "",
                        reference_image_path=ref_path,
                        reference_weight=80,
                        output_path=output_path,
                    )

                    if success:
                        t.status = "completed"
                        t.result_path = output_path
                        t.complete_time = datetime.utcnow()
                        t.review_status = "pending_review"
                        completed_count += 1

                        # 自动创建模板图记录（进入审核队列）
                        template_img = TemplateImage(
                            generate_task_id=t.id,
                            crowd_type=t.crowd_type,
                            style_name=t.style_name,
                            original_path=output_path,
                            final_status="selected",
                        )
                        task_db.add(template_img)

                        # 更新 per_image 进度
                        pi = per_image.get(img_id, {})
                        pi["completed"] = pi.get("completed", 0) + 1
                        pi["progress"] = int(pi["completed"] / pi.get("total", 1) * 100)

                    else:
                        t.status = "failed"
                        t.retry_count += 1
                        failed_count += 1

                        pi = per_image.get(img_id, {})
                        pi["failed"] = pi.get("failed", 0) + 1
                        if fail_codes:
                            for code in fail_codes:
                                reason_stats[code] = reason_stats.get(code, 0) + 1

                    task_db.commit()

                finally:
                    task_db.close()

                # 更新总进度
                done = completed_count + failed_count
                progress = int(done / total * 100)

                ct_name = CROWD_TYPES.get(task_obj.crowd_type, task_obj.crowd_type)
                status_str = "[OK]" if success else "[FAIL]"

                current = ps.get(TASK_TYPE, batch_id)
                current.update({
                    "progress": progress,
                    "completed": completed_count,
                    "failed": failed_count,
                    "per_image": per_image,
                    "reason_stats": reason_stats,
                })
                logs = current.get("logs", [])
                if success:
                    logs.append(f"{status_str} {ct_name}-{task_obj.style_name}")
                else:
                    detail = fail_detail or "未知失败"
                    logs.append(f"{status_str} {ct_name}-{task_obj.style_name} | {detail}")
                    if ("insufficient_user_quota" in (fail_codes or [])) and (not quota_alerted):
                        logs.append(
                            "[ALERT] 检测到上游额度不足（insufficient_user_quota）：请充值 API易 或更换可用 Key 后再重试。"
                        )
                        quota_alerted = True
                current["logs"] = logs
                ps.set(TASK_TYPE, batch_id, current)

        # 并发执行���有任务
        await asyncio.gather(*[process_task(t) for t in tasks])

        if ps.is_cancel_requested(TASK_TYPE, batch_id):
            reset_count = db.query(GenerateTask).join(BaseImage).filter(
                BaseImage.batch_id == batch_id,
                GenerateTask.status == "processing",
            ).update({GenerateTask.status: "pending"}, synchronize_session=False)
            if reset_count > 0:
                db.commit()

            ps.cancel(
                TASK_TYPE,
                batch_id,
                completed_count,
                failed_count,
                f"批量生图已中断：已完成 {completed_count}，失败 {failed_count}，剩余任务保留为待处理",
                per_image=per_image,
                reason_stats=reason_stats,
            )
            return

        # 更新批次状态
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if batch:
            batch.status = "completed" if failed_count == 0 else "ongoing"
            db.commit()

        summary = (
            "批量生图完成！成功 "
            f"{completed_count} 张，失败 {failed_count} 张"
        )
        if reason_stats:
            detail = ", ".join(
                [f"{k}={v}" for k, v in sorted(reason_stats.items(), key=lambda x: (-x[1], x[0]))]
            )
            summary += f" | 失败原因统计: {detail}"

        ps.finish(
            TASK_TYPE,
            batch_id,
            completed_count,
            failed_count,
            summary,
            per_image=per_image,
            reason_stats=reason_stats,
        )

    except Exception as e:
        logger.error(f"批量生图失败 {batch_id}: {e}")
        ps.fail(TASK_TYPE, batch_id, f"生图出错: {str(e)}")
    finally:
        if generator:
            try:
                await generator.close()
            except Exception:
                pass
        db.close()


@router.post("/start", response_model=BaseResponse)
async def start_generation(request: GenerateRequest, db: Session = Depends(get_db)):
    """
    开始批量生图任务（异步后台）
    - 并发调用 SeedDream 4.5 / Nano Banana Pro
    - 智能并发控制 (10-50线程)
    - 失败自动重试2次
    """
    batch = db.query(Batch).filter(Batch.id == request.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    current = ps.get(TASK_TYPE, request.batch_id)
    if current.get("status") in ("running", "cancelling"):
        return BaseResponse(code=1, message="该批次正在生图中")
    ps.clear_cancel(TASK_TYPE, request.batch_id)

    # 统计待生成任务
    pending = db.query(GenerateTask).join(BaseImage).filter(
        BaseImage.batch_id == request.batch_id,
        GenerateTask.status.in_(["pending", "failed"]),
    ).count()

    if pending == 0:
        return BaseResponse(code=1, message="没有待生成的任务，请先生成提示词")

    engine = (
        request.engine
        or get_setting_value(db, "generate_engine", settings.IMAGE_GENERATION_ENGINE)
        or settings.IMAGE_GENERATION_ENGINE
    )

    t = threading.Thread(
        target=_run_generate_background,
        args=(request.batch_id, engine),
        daemon=True,
    )
    t.start()

    return BaseResponse(code=0, message="批量生图已启动", data={
        "batch_id": request.batch_id,
        "pending_count": pending,
        "engine": engine,
    })


@router.get("/progress/{batch_id}", response_model=BaseResponse)
async def get_progress(batch_id: str):
    """获取生图任务进度（含每张底图独立进度）"""
    data = ps.get(TASK_TYPE, batch_id)
    return BaseResponse(code=0, data=data)


@router.post("/cancel/{batch_id}", response_model=BaseResponse)
async def cancel_generation(batch_id: str):
    """中断批量生图任务"""
    if ps.request_cancel(TASK_TYPE, batch_id, "用户请求中断批量生图"):
        return BaseResponse(code=0, message="已发送中断请求，当前任务将在安全点停止")
    return BaseResponse(code=1, message="当前没有运行中的批量生图任务")


@router.post("/retry", response_model=BaseResponse)
async def retry_failed(request: GenerateRequest, db: Session = Depends(get_db)):
    """重试失败的生图任务"""
    current = ps.get(TASK_TYPE, request.batch_id)
    if current.get("status") in ("running", "cancelling"):
        return BaseResponse(code=1, message="当前批次仍在运行中，请先等待或中断后再重试")

    failed_tasks = db.query(GenerateTask).join(BaseImage).filter(
        BaseImage.batch_id == request.batch_id,
        GenerateTask.status == "failed",
    ).all()

    if not failed_tasks:
        return BaseResponse(code=1, message="没有失败的任务")

    # 重置状态为 pending
    for t in failed_tasks:
        t.status = "pending"
    db.commit()

    engine = (
        request.engine
        or get_setting_value(db, "generate_engine", settings.IMAGE_GENERATION_ENGINE)
        or settings.IMAGE_GENERATION_ENGINE
    )

    t = threading.Thread(
        target=_run_generate_background,
        args=(request.batch_id, engine),
        daemon=True,
    )
    ps.clear_cancel(TASK_TYPE, request.batch_id)
    t.start()

    return BaseResponse(code=0, message=f"正在重试 {len(failed_tasks)} 个失败任务", data={
        "retry_count": len(failed_tasks),
    })


@router.get("/overview/{batch_id}", response_model=BaseResponse)
async def get_overview(batch_id: str, db: Session = Depends(get_db)):
    """获取生图任务概览"""
    base_count = db.query(func.count(BaseImage.id)).filter(
        BaseImage.batch_id == batch_id,
        BaseImage.status == "completed",
    ).scalar()

    total_tasks = db.query(func.count(GenerateTask.id)).join(BaseImage).filter(
        BaseImage.batch_id == batch_id,
    ).scalar()

    completed_tasks = db.query(func.count(GenerateTask.id)).join(BaseImage).filter(
        BaseImage.batch_id == batch_id,
        GenerateTask.status == "completed",
    ).scalar()

    failed_tasks = db.query(func.count(GenerateTask.id)).join(BaseImage).filter(
        BaseImage.batch_id == batch_id,
        GenerateTask.status == "failed",
    ).scalar()

    pending_tasks = db.query(func.count(GenerateTask.id)).join(BaseImage).filter(
        BaseImage.batch_id == batch_id,
        GenerateTask.status == "pending",
    ).scalar()

    return BaseResponse(code=0, data={
        "base_images": base_count,
        "crowd_types": 19,
        "styles_per_type": 5,
        "total_tasks": total_tasks,
        "completed": completed_tasks,
        "failed": failed_tasks,
        "pending": pending_tasks,
        "progress": int(completed_tasks / total_tasks * 100) if total_tasks > 0 else 0,
    })
