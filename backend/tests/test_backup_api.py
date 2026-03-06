"""Backup API tests."""
from app.core.encryption import decrypt_value, encrypt_value
from app.models.database import PromptTemplate, Settings


def test_backup_export_returns_settings_and_prompts(client, db_session):
    db_session.add(Settings(
        key="prompt_api_key",
        value=encrypt_value("sk-test-1234"),
        description="阿里百炼API Key",
    ))
    db_session.add(Settings(
        key="export_default_dir",
        value="/tmp/export",
        description="默认导出目录",
    ))
    db_session.add(PromptTemplate(
        crowd_type="C02",
        style_name="法式轻礼服",
        positive_prompt="soft light portrait",
        negative_prompt="blurry, low quality",
        reference_weight=92,
        preferred_engine="seedream",
        is_active=True,
    ))
    db_session.commit()

    resp = client.get("/api/backup/export")
    assert resp.status_code == 200

    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "备份导出成功"

    data = body["data"]
    assert data["version"] == "backup.v1"
    assert data["summary"]["settings_count"] == 2
    assert data["summary"]["prompt_count"] == 1

    settings_map = {item["key"]: item for item in data["settings"]}
    assert settings_map["prompt_api_key"]["value"] == "sk-test-1234"
    assert settings_map["export_default_dir"]["value"] == "/tmp/export"

    prompt_item = data["prompt_templates"][0]
    assert prompt_item["crowd_type"] == "C02"
    assert prompt_item["style_name"] == "法式轻礼服"
    assert prompt_item["reference_weight"] == 92


def test_backup_import_overwrites_settings_and_prompts(client, db_session):
    db_session.add(Settings(
        key="prompt_api_key",
        value=encrypt_value("old-secret"),
        description="旧值",
    ))
    db_session.add(PromptTemplate(
        crowd_type="C01",
        style_name="旧风格",
        positive_prompt="old prompt",
        negative_prompt="",
        reference_weight=80,
        preferred_engine="seedream",
        is_active=True,
    ))
    db_session.commit()

    payload = {
        "backup": {
            "version": "backup.v1",
            "exported_at": "2026-03-06T12:00:00",
            "app_name": "AI图片批量生成系统",
            "contains_secrets": True,
            "settings": [
                {
                    "key": "prompt_api_key",
                    "value": "new-secret",
                    "description": "新的 API Key",
                },
                {
                    "key": "export_default_dir",
                    "value": "/Users/mac/Downloads/backups",
                    "description": "默认导出目录",
                },
            ],
            "prompt_templates": [
                {
                    "crowd_type": "C02",
                    "style_name": "珍珠发钗",
                    "positive_prompt": "pearl hairpin, silk dress",
                    "negative_prompt": "deformed, low quality",
                    "reference_weight": 95,
                    "preferred_engine": "seedream",
                    "is_active": True,
                },
                {
                    "crowd_type": "C06",
                    "style_name": "新中式男装",
                    "positive_prompt": "clean menswear portrait",
                    "negative_prompt": "",
                    "reference_weight": 88,
                    "preferred_engine": "nanobanana",
                    "is_active": False,
                },
            ],
        },
        "restore_settings": True,
        "restore_prompts": True,
    }

    resp = client.post("/api/backup/import", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    assert body["code"] == 0
    assert "备份导入成功" in body["message"]
    assert body["data"]["settings_updated"] == 1
    assert body["data"]["settings_created"] == 1
    assert body["data"]["prompts_deleted"] == 1
    assert body["data"]["prompts_created"] == 2

    prompt_key = db_session.query(Settings).filter(Settings.key == "prompt_api_key").one()
    assert decrypt_value(prompt_key.value) == "new-secret"

    export_dir = db_session.query(Settings).filter(Settings.key == "export_default_dir").one()
    assert export_dir.value == "/Users/mac/Downloads/backups"

    prompts = db_session.query(PromptTemplate).order_by(PromptTemplate.crowd_type.asc()).all()
    assert len(prompts) == 2
    assert prompts[0].style_name == "珍珠发钗"
    assert prompts[1].preferred_engine == "nanobanana"
    assert prompts[1].is_active is False
