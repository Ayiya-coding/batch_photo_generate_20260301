"""
数据库模型定义
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, Enum, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import enum
import uuid

Base = declarative_base()


class ImageStatusEnum(str, enum.Enum):
    """图片状态枚举"""
    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    DISCARDED = "discarded"  # 已废弃


class ReviewStatusEnum(str, enum.Enum):
    """审核状态枚举"""
    PENDING_REVIEW = "pending_review"  # 待审核
    SELECTED = "selected"  # 选用
    PENDING_MODIFICATION = "pending_modification"  # 待修改
    NOT_SELECTED = "not_selected"  # 不选用


class Batch(Base):
    """批次表"""
    __tablename__ = "batches"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    create_time = Column(DateTime, default=datetime.utcnow, index=True)
    update_time = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(String(50), default="ongoing")  # ongoing / completed / failed
    total_images = Column(Integer, default=0)

    # 关系
    base_images = relationship("BaseImage", back_populates="batch", cascade="all, delete-orphan")


class BaseImage(Base):
    """底图表"""
    __tablename__ = "base_images"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id = Column(String(36), ForeignKey("batches.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    original_path = Column(String(512), nullable=False)
    processed_path = Column(String(512), nullable=True)
    upload_time = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default="pending")  # pending/processing/completed/failed/discarded
    preprocess_mode = Column(String(50), default="crop")  # expand / crop
    watermark_removed = Column(Boolean, default=False)
    retry_count = Column(Integer, default=0)

    # 关系
    batch = relationship("Batch", back_populates="base_images")
    generate_tasks = relationship("GenerateTask", back_populates="base_image", cascade="all, delete-orphan")


class GenerateTask(Base):
    """生成任务表"""
    __tablename__ = "generate_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    base_image_id = Column(String(36), ForeignKey("base_images.id"), nullable=False, index=True)
    crowd_type = Column(String(50), nullable=False, index=True)  # C01-C19
    style_name = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=True)
    negative_prompt = Column(Text, nullable=True)
    ai_engine = Column(String(50), nullable=False)  # seedream / nanobanana
    result_path = Column(String(512), nullable=True)
    status = Column(String(50), default="pending")  # pending/processing/completed/failed
    review_status = Column(String(50), default="pending_review")  # pending_review/selected/pending_modification/not_selected
    create_time = Column(DateTime, default=datetime.utcnow)
    complete_time = Column(DateTime, nullable=True)
    retry_count = Column(Integer, default=0)

    # 关系
    base_image = relationship("BaseImage", back_populates="generate_tasks")
    template_image = relationship("TemplateImage", back_populates="generate_task", uselist=False, cascade="all, delete-orphan")


class TemplateImage(Base):
    """模板图表"""
    __tablename__ = "template_images"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    generate_task_id = Column(String(36), ForeignKey("generate_tasks.id"), nullable=False, index=True)
    crowd_type = Column(String(50), nullable=False, index=True)
    style_name = Column(String(255), nullable=False)
    original_path = Column(String(512), nullable=False)
    wide_face_path = Column(String(512), nullable=True)
    wide_face_status = Column(String(50), default="none")  # none/pending/processing/completed/failed
    compress_status = Column(String(50), default="none")  # none/pending/processing/completed/failed
    compressed_path = Column(String(512), nullable=True)
    compressed_wide_face_path = Column(String(512), nullable=True)
    compress_time = Column(DateTime, nullable=True)
    final_status = Column(String(50), default="selected")  # selected / pending_modification / trash
    replaced_from = Column(String(512), nullable=True)  # 替换来源
    create_time = Column(DateTime, default=datetime.utcnow)

    # 关系
    generate_task = relationship("GenerateTask", back_populates="template_image")


class PromptTemplate(Base):
    """提示词模板表"""
    __tablename__ = "prompt_templates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    base_image_id = Column(String(36), ForeignKey("base_images.id"), nullable=True, index=True)  # 关联底图，nullable用于兼容旧数据
    crowd_type = Column(String(50), nullable=False, index=True)
    style_name = Column(String(255), nullable=False)
    positive_prompt = Column(Text, nullable=False)
    negative_prompt = Column(Text, nullable=True)
    reference_weight = Column(Integer, default=80)  # 0-100
    preferred_engine = Column(String(50), default="seedream")
    is_active = Column(Boolean, default=True)
    create_time = Column(DateTime, default=datetime.utcnow)


class Settings(Base):
    """系统设置表"""
    __tablename__ = "settings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String(255), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    update_time = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskProgress(Base):
    """任务进度表 — 持久化各类后台任务的进度，服务重启后可恢复"""
    __tablename__ = "task_progress"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_type = Column(String(50), nullable=False, index=True)  # preprocess/prompt/generate/compress/export/wideface
    task_key = Column(String(100), nullable=False, index=True)  # batch_id or "current"
    status = Column(String(50), default="not_started")  # not_started/running/completed/error
    progress = Column(Integer, default=0)
    total = Column(Integer, default=0)
    completed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    logs = Column(Text, default="[]")  # JSON array of log strings
    extra = Column(Text, default="{}")  # JSON dict for per_image etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
