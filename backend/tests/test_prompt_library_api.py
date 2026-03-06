"""Prompt library export/import/task creation tests."""
from app.models.database import Batch, BaseImage, GenerateTask, PromptTemplate, TemplateImage


def _make_batch_with_images(db_session, image_count=2):
    batch = Batch(name="prompt-library-batch", status="ongoing", total_images=image_count)
    db_session.add(batch)
    db_session.flush()

    images = []
    for index in range(image_count):
        image = BaseImage(
            batch_id=batch.id,
            filename=f"base_{index + 1}.jpg",
            original_path=f"/tmp/base_{index + 1}.jpg",
            processed_path=f"/tmp/base_{index + 1}_processed.jpg",
            status="completed",
        )
        db_session.add(image)
        images.append(image)

    db_session.flush()
    return batch, images


def test_prompt_library_export_only_returns_active_prompts(client, db_session):
    db_session.add(PromptTemplate(
        crowd_type="C02",
        style_name="丝绸礼服",
        positive_prompt="silk dress portrait",
        negative_prompt="bad anatomy",
        is_active=True,
    ))
    db_session.add(PromptTemplate(
        crowd_type="C03",
        style_name="旧款风格",
        positive_prompt="legacy style",
        negative_prompt="",
        is_active=False,
    ))
    db_session.commit()

    resp = client.get("/api/prompt/export")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "提示词库导出成功"
    assert body["data"]["summary"]["prompt_count"] == 1
    assert body["data"]["prompts"][0]["style_name"] == "丝绸礼服"


def test_prompt_library_import_replaces_existing_prompts(client, db_session):
    db_session.add(PromptTemplate(
        crowd_type="C01",
        style_name="旧风格",
        positive_prompt="old prompt",
        negative_prompt="",
        is_active=True,
    ))
    db_session.commit()

    payload = {
        "library": {
            "version": "prompt-library.v1",
            "exported_at": "2026-03-06T12:00:00+00:00",
            "app_name": "AI图片批量生成系统",
            "prompts": [
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
                    "is_active": True,
                },
            ],
        },
        "replace_existing": True,
    }

    resp = client.post("/api/prompt/import", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["deleted_count"] == 1
    assert body["data"]["created_count"] == 2

    prompts = db_session.query(PromptTemplate).order_by(PromptTemplate.crowd_type.asc()).all()
    assert len(prompts) == 2
    assert prompts[0].style_name == "珍珠发钗"
    assert prompts[1].preferred_engine == "nanobanana"


def test_prompt_bulk_upsert_replaces_single_crowd_templates(client, db_session):
    db_session.add(PromptTemplate(
        crowd_type="C02",
        style_name="旧风格A",
        positive_prompt="old prompt a",
        negative_prompt="",
        is_active=True,
    ))
    db_session.add(PromptTemplate(
        crowd_type="C02",
        style_name="旧风格B",
        positive_prompt="old prompt b",
        negative_prompt="",
        is_active=True,
    ))
    db_session.commit()

    resp = client.post("/api/prompt/bulk-upsert", json={
        "crowd_type": "C02",
        "replace_current": True,
        "items": [
            {
                "style_name": "珍珠发钗",
                "positive_prompt": "pearl hairpin portrait",
                "negative_prompt": "blur",
                "reference_weight": 95,
                "preferred_engine": "seedream",
                "is_active": True,
            },
            {
                "style_name": "学院洋装",
                "positive_prompt": "academy dress portrait",
                "negative_prompt": "",
                "reference_weight": 88,
                "preferred_engine": "nanobanana",
                "is_active": True,
            },
        ],
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["created_count"] == 2
    assert body["data"]["updated_count"] == 0
    assert body["data"]["total"] == 2

    prompts = db_session.query(PromptTemplate).filter(
        PromptTemplate.crowd_type == "C02",
    ).order_by(PromptTemplate.style_name.asc()).all()
    active_prompts = [prompt for prompt in prompts if prompt.is_active]
    inactive_prompts = [prompt for prompt in prompts if not prompt.is_active]

    assert len(active_prompts) == 2
    assert {prompt.style_name for prompt in active_prompts} == {"珍珠发钗", "学院洋装"}
    assert len(inactive_prompts) == 2


def test_prompt_library_create_tasks_rebuilds_batch_tasks(client, db_session):
    batch, images = _make_batch_with_images(db_session, image_count=2)
    db_session.add(PromptTemplate(
        crowd_type="C02",
        style_name="法式轻礼服",
        positive_prompt="soft light portrait",
        negative_prompt="blurry",
        preferred_engine="seedream",
        is_active=True,
    ))
    db_session.add(PromptTemplate(
        crowd_type="C06",
        style_name="新中式男装",
        positive_prompt="menswear portrait",
        negative_prompt="low quality",
        preferred_engine="nanobanana",
        is_active=True,
    ))
    db_session.flush()

    old_task = GenerateTask(
        base_image_id=images[0].id,
        crowd_type="C02",
        style_name="旧任务",
        prompt="old task prompt",
        negative_prompt="",
        ai_engine="seedream",
        status="completed",
    )
    db_session.add(old_task)
    db_session.flush()
    db_session.add(TemplateImage(
        generate_task_id=old_task.id,
        crowd_type="C02",
        style_name="旧任务",
        original_path="/tmp/old_result.jpg",
        final_status="selected",
    ))
    db_session.commit()

    resp = client.post("/api/prompt/create-tasks", json={
        "batch_id": batch.id,
        "clear_existing": True,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["pending_count"] == 4
    assert body["data"]["template_count"] == 2

    tasks = db_session.query(GenerateTask).order_by(
        GenerateTask.base_image_id.asc(),
        GenerateTask.crowd_type.asc(),
    ).all()
    assert len(tasks) == 4
    assert all(task.status == "pending" for task in tasks)
    assert all("严格参考上传图片" in task.prompt for task in tasks)
    assert {task.ai_engine for task in tasks} == {"seedream", "nanobanana"}
    assert db_session.query(TemplateImage).count() == 0
