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
from app.schemas.common import WideFaceGenerateRequest, WideFaceReviewRequest, BaseResponse
from app.models.database import TemplateImage
from app.services import progress_store as ps

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_TYPE = "wideface"
TASK_KEY = "current"
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


def _fallback_face_rect(image_shape: tuple[int, int]) -> tuple[int, int, int, int]:
    h, w = image_shape
    fw = int(w * 0.34)
    fh = int(h * 0.28)
    x = int((w - fw) * 0.5)
    y = int(h * 0.18)
    return x, y, fw, fh


def _warp_face_wider(
    image: "np.ndarray",
    face_rect: tuple[int, int, int, int],
    strength: float = 0.30,
) -> "np.ndarray":
    if cv2 is None or np is None:
        return image

    h, w = image.shape[:2]
    x, y, fw, fh = face_rect
    pad_w = int(fw * 0.85)
    pad_h = int(fh * 0.9)
    x0 = max(0, x - pad_w)
    x1 = min(w, x + fw + pad_w)
    y0 = max(0, y - pad_h)
    y1 = min(h, y + fh + pad_h)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return image

    roi = image[y0:y1, x0:x1].copy()
    rh, rw = roi.shape[:2]
    gx, gy = np.meshgrid(np.arange(rw, dtype=np.float32), np.arange(rh, dtype=np.float32))

    # 在 ROI 中使用原 face 的中心作为拉伸中心
    cx = float((x + fw / 2) - x0)
    cy = float((y + fh * 0.52) - y0)
    nx = (gx - cx) / max(8.0, fw * 0.75)
    ny = (gy - cy) / max(8.0, fh * 0.95)
    r2 = nx * nx + ny * ny
    influence = np.exp(-r2 * 2.2)
    scale = 1.0 + strength * influence
    map_x = cx + (gx - cx) / scale
    map_y = gy

    warped = cv2.remap(
        roi,
        map_x.astype(np.float32),
        map_y.astype(np.float32),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )

    # 用椭圆软蒙版混合，避免硬边
    mask = np.zeros((rh, rw), dtype=np.float32)
    axes = (max(18, int((x1 - x0) * 0.38)), max(14, int((y1 - y0) * 0.36)))
    cv2.ellipse(mask, (int(cx), int(cy)), axes, 0, 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=9, sigmaY=9)
    mask3 = mask[..., None]

    blended = (roi.astype(np.float32) * (1.0 - mask3) + warped.astype(np.float32) * mask3).astype(np.uint8)
    result = image.copy()
    result[y0:y1, x0:x1] = blended
    return result


def _enforce_wideface_effect(
    original_path: str,
    generated_path: str,
    min_ratio: float = 1.08,
    target_ratio: float = 1.18,
) -> tuple[bool, str]:
    """
    宽脸兜底：若 AI 结果面宽提升不足，则做一次局部几何增强，确保“看得出变宽”。
    """
    if cv2 is None or np is None:
        return False, "opencv-not-available"
    if not generated_path:
        return False, "generated-path-empty"

    gen_img = cv2.imread(generated_path)
    if gen_img is None:
        return False, "generated-read-failed"

    src_face = None
    if original_path:
        src_img = cv2.imread(original_path)
        if src_img is not None:
            src_face = _detect_primary_face(src_img)
    gen_face = _detect_primary_face(gen_img)

    base_w = float(src_face[2]) if src_face else 0.0
    gen_w = float(gen_face[2]) if gen_face else 0.0
    ratio = (gen_w / base_w) if (base_w > 1 and gen_w > 1) else 0.0
    if ratio >= min_ratio:
        return True, f"ratio={ratio:.3f}"

    face_rect = gen_face
    if face_rect is None and src_face is not None:
        sh, sw = src_img.shape[:2]
        gh, gw = gen_img.shape[:2]
        sx, sy, sww, shh = src_face
        face_rect = (
            int(sx * gw / max(1, sw)),
            int(sy * gh / max(1, sh)),
            int(sww * gw / max(1, sw)),
            int(shh * gh / max(1, sh)),
        )
    if face_rect is None:
        face_rect = _fallback_face_rect((gen_img.shape[0], gen_img.shape[1]))

    # 根据当前比例自动给力度；无法估算比例时用中等力度
    strength = 0.30
    if ratio > 0:
        strength = min(0.52, max(0.26, 0.24 + (target_ratio - ratio) * 0.95))

    enhanced = _warp_face_wider(gen_img, face_rect, strength=strength)
    enhanced_face = _detect_primary_face(enhanced)
    if enhanced_face and src_face:
        enhanced_ratio = enhanced_face[2] / max(1.0, src_face[2])
        # 若第一轮不足，补一轮轻量增强
        if enhanced_ratio < min_ratio:
            enhanced = _warp_face_wider(enhanced, enhanced_face, strength=min(0.56, strength + 0.08))

    ok = cv2.imwrite(generated_path, enhanced, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return (ok, "post-warp-applied" if ok else "save-failed")


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


def _build_wideface_prompt(base_prompt: str) -> str:
    """
    构造更强约束的宽脸编辑提示词，避免“基本看不出变宽”。
    """
    base = (base_prompt or "").strip()
    enforced = (
        "Edit the reference portrait only. Keep the same person identity, hairstyle, outfit, lighting and "
        "background. Increase face width and jaw width by about 22-32%, keep face height almost unchanged, "
        "make cheeks visibly fuller, maintain realistic skin texture, no caricature, no body reshaping."
    )
    if not base:
        return enforced
    return f"{base}. {enforced}"


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

                    prompt = _build_wideface_prompt(wideface_prompt)
                    negative_prompt = (
                        "slim face, narrow jaw, thin cheeks, tiny face, unchanged face width, no widening effect, "
                        "deformed anatomy, big head, "
                        "cartoon, blurry, low quality, text, watermark"
                    )

                    success, fail_detail, fail_codes = await generator.generate_single_with_retry_detail(
                        engine=engine,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        reference_image_path=tmpl.original_path,
                        reference_weight=85,
                        output_path=out_path,
                    )

                    if success:
                        enforced_ok, enforced_msg = _enforce_wideface_effect(
                            original_path=tmpl.original_path,
                            generated_path=out_path,
                            min_ratio=1.08,
                            target_ratio=1.18,
                        )
                        if not enforced_ok:
                            logger.warning("宽脸后处理未生效: %s | %s", tmpl.id, enforced_msg)
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
