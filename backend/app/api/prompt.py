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


def _run_prompt_gen_background(batch_id: str, crowd_type_ids: list):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_generate_prompts(batch_id, crowd_type_ids))
    finally:
        loop.close()


async def _async_generate_prompts(batch_id: str, crowd_type_ids: list):
    """异步批量生成提示词 — 分两阶段：1) 调API生成模板 2) 为所有底图创建任务"""
    db = SessionLocal()
    try:
        from app.services.prompt_generator import PromptGenerator, DEFAULT_STYLES

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
        template_count = len(crowd_type_ids) * len(styles)
        task_count = len(base_images) * template_count

        ps.init(TASK_TYPE, batch_id, template_count,
                f"阶段1: 生成提示词模板 ({len(crowd_type_ids)} 类型 × {len(styles)} 风格 = {template_count} 条)")
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
                    positive, negative = await generator.generate_single(ct_id, style)

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
                        db.add(GenerateTask(
                            base_image_id=img.id,
                            crowd_type=ct_id,
                            style_name=style["name"],
                            prompt=tmpl.positive_prompt if tmpl else "",
                            negative_prompt=tmpl.negative_prompt if tmpl else "",
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
        args=(request.batch_id, crowd_type_ids),
        daemon=True,
    )
    ps.clear_cancel(TASK_TYPE, request.batch_id)
    t.start()

    return BaseResponse(code=0, message="提示词生成已启动", data={
        "batch_id": request.batch_id,
        "crowd_types_count": len(crowd_type_ids),
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
