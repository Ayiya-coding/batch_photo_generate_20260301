"""
数据库迁移脚本：为 PromptTemplate 表添加 base_image_id 字段

执行步骤：
1. 添加 base_image_id 字段（nullable）
2. 为现有提示词创建副本（每张底图复制一份）
3. 软删除旧的全局提示词（base_image_id IS NULL）

使用方法：
    python backend/migrations/add_base_image_to_prompt.py
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "backend"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.models.database import Base, PromptTemplate, BaseImage
import uuid
from datetime import datetime


def run_migration():
    """执行数据库迁移"""
    print("=" * 60)
    print("开始数据库迁移：为 PromptTemplate 添加 base_image_id 字段")
    print("=" * 60)

    # 创建数据库连接
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        # 步骤1：检查字段是否已存在
        print("\n[1/4] 检查 base_image_id 字段是否已存在...")
        result = db.execute(text("""
            SELECT COUNT(*) as cnt
            FROM pragma_table_info('prompt_templates')
            WHERE name='base_image_id'
        """))
        field_exists = result.fetchone()[0] > 0

        if field_exists:
            print("✓ base_image_id 字段已存在，跳过添加步骤")
        else:
            print("✗ base_image_id 字段不存在，开始添加...")
            db.execute(text("""
                ALTER TABLE prompt_templates
                ADD COLUMN base_image_id VARCHAR(36)
            """))
            db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_prompt_templates_base_image_id
                ON prompt_templates(base_image_id)
            """))
            db.commit()
            print("✓ 已添加 base_image_id 字段和索引")

        # 步骤2：统计现有数据
        print("\n[2/4] 统计现有数据...")
        old_prompts = db.query(PromptTemplate).filter(
            PromptTemplate.base_image_id == None,
            PromptTemplate.is_active == True
        ).all()
        print(f"✓ 找到 {len(old_prompts)} 条全局提示词（base_image_id IS NULL）")

        if len(old_prompts) == 0:
            print("✓ 没有需要迁移的旧数据")
            print("\n" + "=" * 60)
            print("迁移完成！")
            print("=" * 60)
            return

        # 获取所有底图
        base_images = db.query(BaseImage).filter(BaseImage.status == "completed").all()
        print(f"✓ 找到 {len(base_images)} 张已完成的底图")

        if len(base_images) == 0:
            print("⚠ 没有找到底图，跳过数据复制步骤")
        else:
            # 步骤3：为每张底图复制提示词
            print(f"\n[3/4] 为每张底图复制提示词...")
            total_created = 0

            for img in base_images:
                created_count = 0
                for old_prompt in old_prompts:
                    # 检查是否已存在
                    existing = db.query(PromptTemplate).filter(
                        PromptTemplate.base_image_id == img.id,
                        PromptTemplate.crowd_type == old_prompt.crowd_type,
                        PromptTemplate.style_name == old_prompt.style_name,
                        PromptTemplate.is_active == True
                    ).first()

                    if not existing:
                        new_prompt = PromptTemplate(
                            id=str(uuid.uuid4()),
                            base_image_id=img.id,
                            crowd_type=old_prompt.crowd_type,
                            style_name=old_prompt.style_name,
                            positive_prompt=old_prompt.positive_prompt,
                            negative_prompt=old_prompt.negative_prompt,
                            reference_weight=old_prompt.reference_weight,
                            preferred_engine=old_prompt.preferred_engine,
                            is_active=True,
                            create_time=datetime.utcnow()
                        )
                        db.add(new_prompt)
                        created_count += 1

                total_created += created_count
                print(f"  - 底图 {img.filename}: 创建 {created_count} 条提示词")

            db.commit()
            print(f"✓ 共创建 {total_created} 条新提示词")

        # 步骤4：软删除旧的全局提示词
        print(f"\n[4/4] 软删除旧的全局提示词...")
        for old_prompt in old_prompts:
            old_prompt.is_active = False
        db.commit()
        print(f"✓ 已软删除 {len(old_prompts)} 条旧提示词")

        print("\n" + "=" * 60)
        print("迁移完成！")
        print("=" * 60)
        print(f"总结：")
        print(f"  - 旧提示词数量: {len(old_prompts)}")
        print(f"  - 底图数量: {len(base_images)}")
        print(f"  - 新创建提示词: {total_created if len(base_images) > 0 else 0}")
        print(f"  - 已软删除旧提示词: {len(old_prompts)}")

    except Exception as e:
        print(f"\n✗ 迁移失败: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_migration()
