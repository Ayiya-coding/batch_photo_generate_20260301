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


def _run_prompt_gen_background(batch_id: str, crowd_type_ids: list, base_image_id: Optional[str] = None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_generate_prompts(batch_id, crowd_type_ids, base_image_id))
    finally:
        loop.close()


async def _async_generate_prompts(batch_id: str, crowd_type_ids: list, base_image_id: Optional[str] = None):
    """异步批量生成提示词 — 为每张底图生成独立的提示词模板"""
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

        # 获取底图列表（如果指定了base_image_id则只处理该底图）
        query = db.query(BaseImage).filter(
            BaseImage.batch_id == batch_id,
            BaseImage.status == "completed"
        )
        if base_image_id:
            query = query.filter(BaseImage.id == base_image_id)

        base_images = query.all()

        if not base_images:
            ps.fail(TASK_TYPE, batch_id, "没有已完成预处理的底图")
            return

        styles = DEFAULT_STYLES
        template_count_per_image = len(crowd_type_ids) * len(styles)
        total_template_count = len(base_images) * template_count_per_image

        ps.init(TASK_TYPE, batch_id, total_template_count,
                f"为 {len(base_images)} 张底图生成提示词 ({len(crowd_type_ids)} 类型 × {len(styles)} 风格 × {len(base_images)} 图 = {total_template_count} 条)")
        ps.append_log(TASK_TYPE, batch_id,
                      f"同时创建对应的生成任务")

        # ===== 为每张底图生成独立的提示词模板 =====
        completed_count = 0
        failed_count = 0
        current_idx = 0

        for img in base_images:
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

                        # 检查该底图是否已有该提示词
                        existing = db.query(PromptTemplate).filter(
                            PromptTemplate.base_image_id == img.id,
                            PromptTemplate.crowd_type == ct_id,
                            PromptTemplate.style_name == style["name"],
                        ).first()

                        if existing:
                            existing.positive_prompt = positive
                            existing.negative_prompt = negative
                            existing.is_active = True
                        else:
                            db.add(PromptTemplate(
                                base_image_id=img.id,
                                crowd_type=ct_id,
                                style_name=style["name"],
                                positive_prompt=positive,
                                negative_prompt=negative,
                            ))

                        # 同时创建或更新 GenerateTask
                        existing_task = db.query(GenerateTask).filter(
                            GenerateTask.base_image_id == img.id,
                            GenerateTask.crowd_type == ct_id,
                            GenerateTask.style_name == style["name"],
                        ).first()

                        if not existing_task:
                            db.add(GenerateTask(
                                base_image_id=img.id,
                                crowd_type=ct_id,
                                style_name=style["name"],
                                prompt=positive,
                                negative_prompt=negative,
                                ai_engine=default_generate_engine,
                                status="pending",
                            ))

                        completed_count += 1
                        ps.append_log(TASK_TYPE, batch_id,
                                      f"[OK] {img.filename[:20]}-{CROWD_TYPES.get(ct_id, ct_id)}-{style['name']}")

                    except Exception as e:
                        logger.error(f"提示词生成失败 {img.id}-{ct_id}-{style['name']}: {e}")
                        failed_count += 1
                        ps.append_log(TASK_TYPE, batch_id,
                                      f"[FAIL] {img.filename[:20]}-{CROWD_TYPES.get(ct_id, ct_id)}-{style['name']}: {str(e)[:50]}")

                    db.commit()

                    progress = int(current_idx / total_template_count * 100)
                    ps.update(TASK_TYPE, batch_id,
                              progress=progress, completed=completed_count, failed=failed_count)

                    await asyncio.sleep(0.3)

        ps.finish(TASK_TYPE, batch_id, completed_count, failed_count,
                  f"全部完成！为 {len(base_images)} 张底图生成 {completed_count} 条提示词，失败 {failed_count} 条")

    except Exception as e:
        logger.error(f"提示词生成批次失败 {batch_id}: {e}")
        ps.fail(TASK_TYPE, batch_id, f"生成出错: {str(e)}")
    finally:
        db.close()


@router.post("/generate", response_model=BaseResponse)
async def generate_prompts(request: PromptGenerateRequest, db: Session = Depends(get_db)):
    """
    一键生成提示词（异步后台任务）
    - 为指定底图生成提示词（不指定则为所有底图生成）
    - 每张底图独立生成提示词模板
    - 同时创建对应的 GenerateTask
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
        args=(request.batch_id, crowd_type_ids, request.base_image_id),
        daemon=True,
    )
    ps.clear_cancel(TASK_TYPE, request.batch_id)
    t.start()

    return BaseResponse(code=0, message="提示词生成已启动", data={
        "batch_id": request.batch_id,
        "crowd_types_count": len(crowd_type_ids),
        "base_image_id": request.base_image_id,
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
    base_image_id: Optional[str] = None,
    crowd_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    查看提示词列表
    - 必须指定 base_image_id 来查询某张底图的提示词
    - 可按人群类型筛选
    - 返回提示词模板 + 关联的生成任务数
    """
    query = db.query(PromptTemplate).filter(PromptTemplate.is_active == True)

    # 必须指定 base_image_id
    if base_image_id:
        query = query.filter(PromptTemplate.base_image_id == base_image_id)

    if crowd_type:
        query = query.filter(PromptTemplate.crowd_type == crowd_type)

    templates = query.order_by(PromptTemplate.crowd_type, PromptTemplate.style_name).all()

    result = []
    for t in templates:
        # 统计关联的待生成任务数
        task_count = 0
        if t.base_image_id:
            task_count = db.query(GenerateTask).filter(
                GenerateTask.base_image_id == t.base_image_id,
                GenerateTask.crowd_type == t.crowd_type,
                GenerateTask.style_name == t.style_name,
            ).count()

        result.append({
            "id": t.id,
            "base_image_id": t.base_image_id,
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


@router.delete("/batch-delete", response_model=BaseResponse)
async def batch_delete_prompts(
    batch_id: Optional[str] = None,
    base_image_id: Optional[str] = None,
    crowd_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    批量删除提示词（软删除）

    删除粒度：
    - 传 base_image_id + crowd_type: 删除该底图该人群类型的所有提示词
    - 传 base_image_id: 删除该底图的所有提示词
    - 传 batch_id: 删除整个批次所有底图的所有提示词
    """
    query = db.query(PromptTemplate).filter(PromptTemplate.is_active == True)

    if base_image_id and crowd_type:
        # 删除指定底图的指定人群类型
        query = query.filter(
            PromptTemplate.base_image_id == base_image_id,
            PromptTemplate.crowd_type == crowd_type
        )
    elif base_image_id:
        # 删除指定底图的所有提示词
        query = query.filter(PromptTemplate.base_image_id == base_image_id)
    elif batch_id:
        # 删除整个批次的所有提示词（通过 base_images 表关联）
        base_image_ids = db.query(BaseImage.id).filter(BaseImage.batch_id == batch_id).all()
        image_id_list = [id[0] for id in base_image_ids]
        query = query.filter(PromptTemplate.base_image_id.in_(image_id_list))
    else:
        raise HTTPException(status_code=400, detail="至少需要提供一个过滤条件")

    count = query.update({"is_active": False}, synchronize_session=False)
    db.commit()

    return BaseResponse(code=0, message=f"已删除 {count} 条提示词")
