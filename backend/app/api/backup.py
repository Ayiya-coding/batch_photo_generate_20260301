"""备份恢复 API 路由"""
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.encryption import decrypt_value, encrypt_value, is_api_key_field
from app.models.database import PromptTemplate, Settings
from app.schemas.common import BackupImportRequest, BaseResponse

router = APIRouter()

BACKUP_VERSION = "backup.v1"


def _serialize_settings(db: Session) -> list[dict]:
    items: list[dict] = []
    rows = db.query(Settings).order_by(Settings.key.asc()).all()
    for row in rows:
        value = row.value or ""
        if is_api_key_field(row.key) and value:
            value = decrypt_value(value)
        items.append({
            "key": row.key,
            "value": value,
            "description": row.description or "",
        })
    return items


def _serialize_prompt_templates(db: Session) -> list[dict]:
    items: list[dict] = []
    rows = db.query(PromptTemplate).order_by(
        PromptTemplate.crowd_type.asc(),
        PromptTemplate.style_name.asc(),
        PromptTemplate.create_time.asc(),
    ).all()
    for row in rows:
        items.append({
            "id": row.id,
            "crowd_type": row.crowd_type,
            "style_name": row.style_name,
            "positive_prompt": row.positive_prompt,
            "negative_prompt": row.negative_prompt or "",
            "reference_weight": row.reference_weight,
            "preferred_engine": row.preferred_engine,
            "is_active": bool(row.is_active),
            "create_time": row.create_time,
        })
    return items


@router.get("/export", response_model=BaseResponse)
async def export_backup(db: Session = Depends(get_db)):
    """导出系统设置与提示词词库备份。"""
    settings_data = _serialize_settings(db)
    prompt_templates = _serialize_prompt_templates(db)
    exported_at = datetime.now(timezone.utc)

    return BaseResponse(
        code=0,
        message="备份导出成功",
        data={
            "version": BACKUP_VERSION,
            "exported_at": exported_at.isoformat(),
            "app_name": app_settings.APP_NAME,
            "contains_secrets": True,
            "settings": settings_data,
            "prompt_templates": prompt_templates,
            "summary": {
                "settings_count": len(settings_data),
                "prompt_count": len(prompt_templates),
            },
        },
    )


@router.post("/import", response_model=BaseResponse)
async def import_backup(request: BackupImportRequest, db: Session = Depends(get_db)):
    """导入备份，可选择恢复系统设置和提示词词库。"""
    if not request.restore_settings and not request.restore_prompts:
        raise HTTPException(status_code=400, detail="请至少选择一个恢复项")

    payload = request.backup
    if payload.version != BACKUP_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的备份版本: {payload.version}",
        )

    settings_created = 0
    settings_updated = 0
    prompts_deleted = 0
    prompts_created = 0

    if request.restore_settings:
        existing_settings = {
            item.key: item
            for item in db.query(Settings).all()
        }
        for item in payload.settings:
            value = item.value
            if is_api_key_field(item.key) and value:
                value = encrypt_value(value)

            current = existing_settings.get(item.key)
            if current:
                current.value = value
                current.description = item.description or current.description or ""
                settings_updated += 1
            else:
                db.add(Settings(
                    key=item.key,
                    value=value,
                    description=item.description or "",
                ))
                settings_created += 1

    if request.restore_prompts:
        prompts_deleted = db.query(PromptTemplate).delete(synchronize_session=False)
        db.flush()
        for item in payload.prompt_templates:
            db.add(PromptTemplate(
                id=item.id or str(uuid.uuid4()),
                crowd_type=item.crowd_type,
                style_name=item.style_name,
                positive_prompt=item.positive_prompt,
                negative_prompt=item.negative_prompt or "",
                reference_weight=item.reference_weight,
                preferred_engine=item.preferred_engine or "seedream",
                is_active=item.is_active,
                create_time=item.create_time or datetime.now(timezone.utc),
            ))
            prompts_created += 1

    db.commit()

    restored_parts = []
    if request.restore_settings:
        restored_parts.append("系统设置")
    if request.restore_prompts:
        restored_parts.append("提示词词库")

    return BaseResponse(
        code=0,
        message=f"备份导入成功：已恢复{' + '.join(restored_parts)}",
        data={
            "restore_settings": request.restore_settings,
            "restore_prompts": request.restore_prompts,
            "settings_created": settings_created,
            "settings_updated": settings_updated,
            "prompts_deleted": prompts_deleted,
            "prompts_created": prompts_created,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
