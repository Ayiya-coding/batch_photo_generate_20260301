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
from typing import Optional

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

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
BG_REF_DIR = settings.PROCESSED_DIR / "background_refs"
BG_REF_DIR.mkdir(parents=True, exist_ok=True)

_FACE_CASCADE = None


def _load_face_cascade():
    global _FACE_CASCADE
    if cv2 is None:
        return None
    if _FACE_CASCADE is not None:
        return _FACE_CASCADE
    try:
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not cascade_path.exists():
            return None
        detector = cv2.CascadeClassifier(str(cascade_path))
        if detector.empty():
            return None
        _FACE_CASCADE = detector
        return _FACE_CASCADE
    except Exception:
        return None


def _detect_primary_face(image) -> Optional[tuple[int, int, int, int]]:
    if cv2 is None:
        return None
    detector = _load_face_cascade()
    if detector is None:
        return None
    try:
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        min_edge = max(24, min(h, w) // 14)
        faces = detector.detectMultiScale(
            gray,
            scaleFactor=1.08,
            minNeighbors=4,
            minSize=(min_edge, min_edge),
        )
        if len(faces) == 0:
            return None
        x, y, fw, fh = max(faces, key=lambda item: item[2] * item[3])
        return int(x), int(y), int(fw), int(fh)
    except Exception:
        return None


def _build_subject_mask(image) -> Optional["np.ndarray"]:
    if cv2 is None or np is None:
        return None
    h, w = image.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    face = _detect_primary_face(image)
    if face:
        x, y, fw, fh = face
        cx = x + fw // 2
        top = max(0, y - int(fh * 0.9))
        bottom = min(h, y + int(fh * 5.8))
        half_w = int(fw * 1.9)
        left = max(0, cx - half_w)
        right = min(w, cx + half_w)
    else:
        # 无法稳定检测人脸时，按人像图常见主体区间兜底
        top = int(h * 0.18)
        bottom = h
        left = int(w * 0.18)
        right = int(w * 0.82)
    cv2.rectangle(mask, (left, top), (right, bottom), 255, -1)
    # 头肩区域做额外覆盖，防止原脸残留
    head_cx = (left + right) // 2
    head_cy = top + int((bottom - top) * 0.22)
    cv2.ellipse(
        mask,
        (head_cx, head_cy),
        (max(24, (right - left) // 3), max(20, (bottom - top) // 5)),
        0,
        0,
        360,
        255,
        -1,
    )
    kernel = np.ones((25, 25), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def _render_background_reference(source_path: str, image_id: str) -> str:
    """
    生成“背景参考图”：弱化/抹除主体人像，保留景点、光影、构图，用于后续生图参考。
    """
    if not source_path:
        return source_path
    src = Path(source_path)
    if not src.exists():
        return source_path
    if cv2 is None or np is None:
        return source_path

    out_path = BG_REF_DIR / f"{image_id}_bgref.jpg"
    try:
        if out_path.exists() and out_path.stat().st_mtime >= src.stat().st_mtime:
            return str(out_path)
    except Exception:
        pass

    image = cv2.imread(str(src))
    if image is None:
        return source_path

    mask = _build_subject_mask(image)
    if mask is None:
        return source_path

    try:
        mask_ratio = float((mask > 0).mean())
        # 面积过大时优先模糊，避免大面积 inpaint 伪影；面积适中时 inpaint + 模糊混合
        blurred = cv2.GaussianBlur(image, (61, 61), 0)
        if mask_ratio <= 0.35:
            inpainted = cv2.inpaint(image, mask, 7, cv2.INPAINT_TELEA)
            mixed = cv2.addWeighted(inpainted, 0.72, blurred, 0.28, 0)
        else:
            mixed = blurred

        result = image.copy()
        result[mask > 0] = mixed[mask > 0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), result, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return str(out_path)
    except Exception as e:
        logger.warning("生成背景参考图失败，回退原图: %s", e)
        return source_path


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


def _run_generate_background(batch_id: str, engine: str, strict_reference: bool):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_batch_generate(batch_id, engine, strict_reference))
    finally:
        loop.close()


async def _async_batch_generate(batch_id: str, engine: str, strict_reference: bool):
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
        strict_reference_setting = (
            str(get_setting_value(db, "generate_strict_reference", "1")).strip() != "0"
        )
        strict_reference = bool(strict_reference and strict_reference_setting)
        try:
            reference_weight = int(str(get_setting_value(db, "generate_reference_weight", "95")).strip() or "95")
        except ValueError:
            reference_weight = 95
        reference_weight = max(0, min(100, reference_weight))
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
                    "ref_path": (img.processed_path or img.original_path) if img else "",
                    "total": 0, "completed": 0, "failed": 0,
                }
            image_task_map[t.base_image_id]["total"] += 1

        per_image = {
            img_id: {
                "filename": info.get("filename", "unknown"),
                "total": info.get("total", 0),
                "completed": info.get("completed", 0),
                "failed": info.get("failed", 0),
                "progress": 0,
            }
            for img_id, info in image_task_map.items()
        }
        reason_stats: dict[str, int] = {}
        quota_alerted = False

        # 为每张底图预生成“背景参考图”，弱化人像，锁定景点/光影/构图
        bg_reference_map: dict[str, str] = {}
        for img_id, info in image_task_map.items():
            src_ref = (info.get("ref_path") or "").strip()
            if src_ref:
                bg_reference_map[img_id] = _render_background_reference(src_ref, img_id)
            else:
                bg_reference_map[img_id] = src_ref

        ps.init(TASK_TYPE, batch_id, total,
                f"开始批量生图: {total} 个任务, 引擎={engine}, 严格参考={'开启' if strict_reference else '关闭'}",
                per_image=per_image,
                reason_stats=reason_stats)
        ps.append_log(
            TASK_TYPE,
            batch_id,
            f"[REF] 已准备背景参考图 {len(bg_reference_map)} 张（锁景点/光影，弱化原人像）| reference_weight={reference_weight}",
        )

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
                    ref_bg_path = bg_reference_map.get(img_id) or ref_path
                    output_filename = f"{task_id}_{t.crowd_type}_{t.style_name}.jpg"
                    output_path = str(settings.GENERATED_DIR / output_filename)

                    task_engine = engine or t.ai_engine or settings.IMAGE_GENERATION_ENGINE

                    if strict_reference and (not ref_bg_path or not Path(ref_bg_path).exists()):
                        success = False
                        fail_detail = "严格参考模式缺少有效参考图"
                        fail_codes = ["missing_reference_image"]
                    else:
                        success, fail_detail, fail_codes = await generator.generate_single_with_retry_detail(
                            engine=task_engine,
                            prompt=t.prompt or "",
                            negative_prompt=t.negative_prompt or "",
                            reference_image_path=ref_bg_path,
                            reference_weight=reference_weight,
                            strict_reference=strict_reference,
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

    # 兜底修复：若上次任务异常中断，可能残留 processing 状态，这里统一回滚为 pending
    stuck_task_ids = [
        row[0]
        for row in db.query(GenerateTask.id).join(BaseImage).filter(
            BaseImage.batch_id == request.batch_id,
            GenerateTask.status == "processing",
        ).all()
    ]
    if stuck_task_ids:
        db.query(GenerateTask).filter(GenerateTask.id.in_(stuck_task_ids)).update(
            {GenerateTask.status: "pending"},
            synchronize_session=False,
        )
        db.commit()

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
    strict_reference_setting = (
        str(get_setting_value(db, "generate_strict_reference", "1")).strip() != "0"
    )
    strict_reference = bool(request.strict_reference and strict_reference_setting)

    t = threading.Thread(
        target=_run_generate_background,
        args=(request.batch_id, engine, strict_reference),
        daemon=True,
    )
    t.start()

    return BaseResponse(code=0, message="批量生图已启动", data={
        "batch_id": request.batch_id,
        "pending_count": pending,
        "engine": engine,
        "strict_reference": strict_reference,
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
    strict_reference_setting = (
        str(get_setting_value(db, "generate_strict_reference", "1")).strip() != "0"
    )
    strict_reference = bool(request.strict_reference and strict_reference_setting)

    t = threading.Thread(
        target=_run_generate_background,
        args=(request.batch_id, engine, strict_reference),
        daemon=True,
    )
    ps.clear_cancel(TASK_TYPE, request.batch_id)
    t.start()

    return BaseResponse(code=0, message=f"正在重试 {len(failed_tasks)} 个失败任务", data={
        "retry_count": len(failed_tasks),
        "strict_reference": strict_reference,
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

    crowd_type_count = db.query(func.count(func.distinct(GenerateTask.crowd_type))).join(BaseImage).filter(
        BaseImage.batch_id == batch_id,
    ).scalar()

    style_count = db.query(func.count(func.distinct(GenerateTask.style_name))).join(BaseImage).filter(
        BaseImage.batch_id == batch_id,
    ).scalar()

    return BaseResponse(code=0, data={
        "base_images": base_count,
        "crowd_types": crowd_type_count or 0,
        "styles_per_type": style_count or 0,
        "total_tasks": total_tasks,
        "completed": completed_tasks,
        "failed": failed_tasks,
        "pending": pending_tasks,
        "progress": int(completed_tasks / total_tasks * 100) if total_tasks > 0 else 0,
    })
