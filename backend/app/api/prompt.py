"""
提示词生成API路由
- 一键生成全部类型提示词
- 查看/编辑/删除提示词
- 异步后台生成 + 进度轮询
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
import asyncio
import threading
import logging

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from app.core.database import get_db, SessionLocal
from app.core.config import settings
from app.core.settings_resolver import get_setting_value
from app.core.constants import CROWD_TYPES
from app.schemas.common import PromptGenerateRequest, BaseResponse
from app.models.database import BaseImage, PromptTemplate, GenerateTask
from app.services import progress_store as ps

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_TYPE = "prompt"


def _summarize_reference_image(image_path: str) -> str:
    """
    提取参考底图的基础视觉特征，给提示词生成提供上下文。
    """
    if not image_path or not cv2:
        return ""
    try:
        image = cv2.imread(image_path)
        if image is None:
            return ""

        h, w = image.shape[:2]
        b_mean = float(image[:, :, 0].mean())
        g_mean = float(image[:, :, 1].mean())
        r_mean = float(image[:, :, 2].mean())
        brightness = (r_mean + g_mean + b_mean) / 3.0
        color_delta = r_mean - b_mean

        if brightness >= 185:
            light = "high-key bright lighting"
        elif brightness >= 130:
            light = "balanced natural lighting"
        else:
            light = "low-key dim lighting"

        if color_delta >= 18:
            tone = "warm color tone"
        elif color_delta <= -18:
            tone = "cool color tone"
        else:
            tone = "neutral color tone"

        # 用边缘密度估计背景复杂度
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        edge_density = float((edges > 0).mean())
        if edge_density >= 0.12:
            background = "complex textured background"
        elif edge_density >= 0.06:
            background = "moderate background details"
        else:
            background = "clean minimal background"

        orientation = "portrait orientation" if h >= w else "landscape orientation"
        return f"{orientation}, {light}, {tone}, {background}"
    except Exception as e:  # pragma: no cover
        logger.warning("解析参考底图特征失败: %s", e)
        return ""


def _build_task_prompt(base_prompt: str, style_name: str) -> str:
    """
    在模板提示词上追加“参考背景、替换人物”的硬约束，降低跑偏。
    """
    base = (base_prompt or "").strip()
    guard = (
        "Use the reference image for background scene only. Keep location, camera angle, framing, perspective and "
        "lighting consistent with the reference background. Replace the person identity and facial features with a "
        f"new subject that matches the target crowd and style '{style_name}'. Do not preserve the original face. "
        "Avoid changing background layout or scene structure."
    )
    return f"{base}, {guard}" if base else guard


def _build_task_negative_prompt(base_negative: str) -> str:
    """
    强化负向约束：避免沿用底图原人物脸。
    """
    extra = "same face as reference, same identity as reference person, lookalike clone face, identity preserved from reference"
    base = (base_negative or "").strip()
    return f"{base}, {extra}" if base else extra


def _run_prompt_gen_background(batch_id: str, crowd_type_ids: list, reference_image_id: str | None = None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_generate_prompts(batch_id, crowd_type_ids, reference_image_id))
    finally:
        loop.close()


async def _async_generate_prompts(
    batch_id: str,
    crowd_type_ids: list,
    reference_image_id: str | None = None,
):
    """异步批量生成提示词 — 分两阶段：1) 调API生成模板 2) 为所有底图创建任务"""
    db = SessionLocal()
    try:
        from app.services.prompt_generator import (
            PromptGenerator,
            DEFAULT_STYLES,
            STYLE_VARIATION_HINTS,
        )

        api_key = get_setting_value(db, "prompt_api_key", "")
        system_prompt = get_setting_value(db, "prompt_system_prompt", "")
        default_generate_engine = (
            get_setting_value(db, "generate_engine", settings.IMAGE_GENERATION_ENGINE)
            or settings.IMAGE_GENERATION_ENGINE
        )
        generator = PromptGenerator(api_key=api_key, system_prompt=system_prompt)

        base_images = db.query(BaseImage).filter(
            BaseImage.batch_id == batch_id,
            BaseImage.status == "completed"
        ).all()

        if not base_images:
            ps.fail(TASK_TYPE, batch_id, "没有已完成预处理的底图")
            return

        styles = DEFAULT_STYLES

        ref_image = None
        if reference_image_id:
            ref_image = next((img for img in base_images if img.id == reference_image_id), None)
            if not ref_image:
                ps.append_log(
                    TASK_TYPE,
                    batch_id,
                    f"[WARN] 指定参考底图不存在或不可用: {reference_image_id[:8]}，将使用默认参考图",
                )
        if ref_image is None:
            ref_image = base_images[0]

        ref_path = ref_image.processed_path or ref_image.original_path
        reference_context = _summarize_reference_image(ref_path)
        template_count = len(crowd_type_ids) * len(styles)
        task_count = len(base_images) * template_count

        ps.init(
            TASK_TYPE,
            batch_id,
            template_count,
            (
                f"阶段1: 生成提示词模板 ({len(crowd_type_ids)} 类型 × {len(styles)} 风格 = {template_count} 条) "
                f"| 参考底图: {ref_image.filename}"
            ),
        )
        ps.append_log(TASK_TYPE, batch_id,
                      f"阶段2: 为 {len(base_images)} 张底图创建生成任务 (共 {task_count} 个)")

        # ===== 阶段1: 调用百炼API生成提示词模板 =====
        completed_count = 0
        failed_count = 0
        current_idx = 0

        for ct_id in crowd_type_ids:
            if ps.is_cancel_requested(TASK_TYPE, batch_id):
                ps.cancel(
                    TASK_TYPE,
                    batch_id,
                    completed_count,
                    failed_count,
                    f"提示词生成已中断：已完成 {completed_count}，失败 {failed_count}",
                )
                return
            for style in styles:
                if ps.is_cancel_requested(TASK_TYPE, batch_id):
                    ps.cancel(
                        TASK_TYPE,
                        batch_id,
                        completed_count,
                        failed_count,
                        f"提示词生成已中断：已完成 {completed_count}，失败 {failed_count}",
                    )
                    return
                current_idx += 1
                try:
                    positive, negative = await generator.generate_single(
                        ct_id,
                        style,
                        reference_context=reference_context,
                        style_variation_hint=STYLE_VARIATION_HINTS.get(style["name"], ""),
                    )

                    existing = db.query(PromptTemplate).filter(
                        PromptTemplate.crowd_type == ct_id,
                        PromptTemplate.style_name == style["name"],
                    ).first()

                    if existing:
                        existing.positive_prompt = positive
                        existing.negative_prompt = negative
                    else:
                        db.add(PromptTemplate(
                            crowd_type=ct_id,
                            style_name=style["name"],
                            positive_prompt=positive,
                            negative_prompt=negative,
                        ))

                    completed_count += 1
                    ps.append_log(TASK_TYPE, batch_id,
                                  f"[OK] {CROWD_TYPES.get(ct_id, ct_id)}-{style['name']}")

                except Exception as e:
                    logger.error(f"提示词生成失败 {ct_id}-{style['name']}: {e}")
                    failed_count += 1
                    ps.append_log(TASK_TYPE, batch_id,
                                  f"[FAIL] {CROWD_TYPES.get(ct_id, ct_id)}-{style['name']}: {str(e)[:50]}")

                db.commit()

                progress = int(current_idx / template_count * 80)  # 阶段1占80%进度
                ps.update(TASK_TYPE, batch_id,
                          progress=progress, completed=completed_count, failed=failed_count)

                await asyncio.sleep(0.3)

        # ===== 阶段2: 为所有底图创建 GenerateTask =====
        ps.append_log(TASK_TYPE, batch_id,
                      f"提示词模板完成，正在为 {len(base_images)} 张底图创建生成任务...")

        # 保护：移除“当前选中人群之外”的待生成任务，避免误触发后继续跑95任务
        stale_task_ids = [
            row[0]
            for row in db.query(GenerateTask.id).join(BaseImage).filter(
                BaseImage.batch_id == batch_id,
                ~GenerateTask.crowd_type.in_(crowd_type_ids),
                GenerateTask.status.in_(["pending", "failed", "processing"]),
            ).all()
        ]
        if stale_task_ids:
            db.query(GenerateTask).filter(GenerateTask.id.in_(stale_task_ids)).delete(
                synchronize_session=False
            )
            db.commit()
            ps.append_log(
                TASK_TYPE,
                batch_id,
                f"[CLEANUP] 已清理非当前人群的遗留任务 {len(stale_task_ids)} 条",
            )

        templates = db.query(PromptTemplate).filter(
            PromptTemplate.crowd_type.in_(crowd_type_ids),
            PromptTemplate.is_active == True,
        ).all()

        template_map = {(t.crowd_type, t.style_name): t for t in templates}
        tasks_created = 0

        for img in base_images:
            if ps.is_cancel_requested(TASK_TYPE, batch_id):
                ps.cancel(
                    TASK_TYPE,
                    batch_id,
                    completed_count,
                    failed_count,
                    f"提示词生成已中断：已完成 {completed_count}，失败 {failed_count}，已保留已创建任务",
                )
                return
            for ct_id in crowd_type_ids:
                for style in styles:
                    existing_task = db.query(GenerateTask).filter(
                        GenerateTask.base_image_id == img.id,
                        GenerateTask.crowd_type == ct_id,
                        GenerateTask.style_name == style["name"],
                    ).first()

                    if not existing_task:
                        tmpl = template_map.get((ct_id, style["name"]))
                        task_prompt = _build_task_prompt(
                            tmpl.positive_prompt if tmpl else "",
                            style["name"],
                        )
                        db.add(GenerateTask(
                            base_image_id=img.id,
                            crowd_type=ct_id,
                            style_name=style["name"],
                            prompt=task_prompt,
                            negative_prompt=_build_task_negative_prompt(tmpl.negative_prompt if tmpl else ""),
                            ai_engine=default_generate_engine,
                            status="pending",
                        ))
                        tasks_created += 1

            db.commit()

        ps.finish(TASK_TYPE, batch_id, completed_count, failed_count,
                  f"全部完成！生成 {completed_count} 条提示词模板，创建 {tasks_created} 个生成任务，失败 {failed_count} 条")

    except Exception as e:
        logger.error(f"提示词生成批次失败 {batch_id}: {e}")
        ps.fail(TASK_TYPE, batch_id, f"生成出错: {str(e)}")
    finally:
        db.close()


@router.post("/generate", response_model=BaseResponse)
async def generate_prompts(request: PromptGenerateRequest, db: Session = Depends(get_db)):
    """
    一键生成全部类型提示词（异步后台任务）
    - 19种人群类型 × 5种风格 = 95 条提示词模板
    - 为每张底图创建对应的 GenerateTask
    """
    from app.models.database import Batch
    batch = db.query(Batch).filter(Batch.id == request.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    # 检查是否已在运行
    current = ps.get(TASK_TYPE, request.batch_id)
    if current.get("status") in ("running", "cancelling"):
        return BaseResponse(code=1, message="该批次提示词正在生成中")

    crowd_type_ids = list(dict.fromkeys(request.crowd_types or []))
    if not crowd_type_ids:
        return BaseResponse(code=1, message="请先选择人群类型后再生成提示词")

    t = threading.Thread(
        target=_run_prompt_gen_background,
        args=(request.batch_id, crowd_type_ids, request.reference_image_id),
        daemon=True,
    )
    ps.clear_cancel(TASK_TYPE, request.batch_id)
    t.start()

    return BaseResponse(code=0, message="提示词生成已启动", data={
        "batch_id": request.batch_id,
        "crowd_types_count": len(crowd_type_ids),
        "reference_image_id": request.reference_image_id or "",
    })


@router.get("/progress/{batch_id}", response_model=BaseResponse)
async def get_prompt_progress(batch_id: str):
    """查询提示词生成进度"""
    data = ps.get(TASK_TYPE, batch_id)
    return BaseResponse(code=0, data=data)


@router.post("/cancel/{batch_id}", response_model=BaseResponse)
async def cancel_prompt_generation(batch_id: str):
    """中断提示词生成任务"""
    if ps.request_cancel(TASK_TYPE, batch_id, "用户请求中断提示词生成"):
        return BaseResponse(code=0, message="已发送中断请求，任务将在安全点停止")
    return BaseResponse(code=1, message="当前没有运行中的提示词任务")


@router.get("/list", response_model=BaseResponse)
async def list_prompts(
    batch_id: Optional[str] = None,
    crowd_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    查看提示词列表
    - 可按人群类型筛选
    - 返回提示词模板 + 关联的生成任务数
    """
    query = db.query(PromptTemplate).filter(PromptTemplate.is_active == True)
    if crowd_type:
        query = query.filter(PromptTemplate.crowd_type == crowd_type)

    templates = query.order_by(PromptTemplate.crowd_type, PromptTemplate.style_name).all()

    result = []
    for t in templates:
        # 统计关联的待生成任务数
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

    return BaseResponse(code=0, data={
        "prompts": result,
        "total": len(result),
    })


@router.put("/edit/{prompt_id}", response_model=BaseResponse)
async def edit_prompt(
    prompt_id: str,
    positive_prompt: str = None,
    negative_prompt: str = None,
    style_name: str = None,
    reference_weight: int = None,
    preferred_engine: str = None,
    db: Session = Depends(get_db)
):
    """编辑单条提示词"""
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
        template.reference_weight = max(0, min(100, reference_weight))
    if preferred_engine is not None:
        template.preferred_engine = preferred_engine

    db.commit()
    return BaseResponse(code=0, message="提示词已更新")


@router.delete("/delete/{prompt_id}", response_model=BaseResponse)
async def delete_prompt(prompt_id: str, db: Session = Depends(get_db)):
    """删除提示词（软删除）"""
    template = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="提示词不存在")

    template.is_active = False
    db.commit()
    return BaseResponse(code=0, message="提示词已删除")


@router.delete("/delete-by-crowd/{crowd_type}", response_model=BaseResponse)
async def delete_prompts_by_crowd(crowd_type: str, db: Session = Depends(get_db)):
    """按人群类型批量软删除提示词"""
    if crowd_type not in CROWD_TYPES:
        raise HTTPException(status_code=400, detail=f"无效的人群类型: {crowd_type}")

    templates = db.query(PromptTemplate).filter(
        PromptTemplate.crowd_type == crowd_type,
        PromptTemplate.is_active == True,
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
