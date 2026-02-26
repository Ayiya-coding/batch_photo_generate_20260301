"""
数据库连接和会话管理
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.models.database import Base, Settings

# 创建数据库引擎
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite需要
    echo=settings.DEBUG  # 开发模式下打印SQL
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """初始化数据库（创建所有表）"""
    Base.metadata.create_all(bind=engine)
    _apply_schema_patches()


def get_db():
    """获取数据库会话（用于FastAPI依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _apply_schema_patches():
    """
    兼容历史 SQLite 数据库结构：
    create_all 不会为已存在表自动补列，这里做轻量补丁。
    """
    if engine.url.get_backend_name() != "sqlite":
        return

    patches = {
        "base_images": [
            ("preprocess_mode", "preprocess_mode VARCHAR(50) DEFAULT 'crop'"),
            ("watermark_removed", "watermark_removed BOOLEAN DEFAULT 0"),
            ("retry_count", "retry_count INTEGER DEFAULT 0"),
        ],
        "generate_tasks": [
            ("retry_count", "retry_count INTEGER DEFAULT 0"),
        ],
        "template_images": [
            ("wide_face_status", "wide_face_status VARCHAR(50) DEFAULT 'none'"),
            ("compress_status", "compress_status VARCHAR(50) DEFAULT 'none'"),
            ("compressed_path", "compressed_path VARCHAR(512)"),
            ("compressed_wide_face_path", "compressed_wide_face_path VARCHAR(512)"),
            ("compress_time", "compress_time DATETIME"),
            ("final_status", "final_status VARCHAR(50) DEFAULT 'selected'"),
            ("replaced_from", "replaced_from VARCHAR(512)"),
        ],
    }

    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        for table, columns in patches.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_def in columns:
                if col_name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_def}"))


# 默认系统配置（7项配置组，按产品需求文档定义）
DEFAULT_SETTINGS = [
    # 配置项1：图片去水印配置
    ("watermark_region", "右下角", "默认水印区域(右下角/左下角/右上角/左上角/全图检测)"),
    ("watermark_margin", "15", "水印区域边距比例(5-30%)"),
    ("watermark_engine", "auto", "去水印引擎(auto/iopaint/volc)"),
    ("iopaint_port", "8090", "IOPaint 服务端口"),
    ("gpu_acceleration", "1", "启用GPU加速"),
    ("volc_access_key_id", "", "火山视觉 AccessKeyId"),
    ("volc_secret_access_key", "", "火山视觉 SecretAccessKey"),
    # 配置项2：图片扩图配置
    ("target_ratio", "9:16", "目标画面比例"),
    ("expand_engine", "seedream", "扩图引擎(seedream/iopaint/auto)"),
    ("expand_allow_fallback", "1", "扩图失败时允许降级填充(1=是/0=否)"),
    # 配置项3：提示词生成配置
    ("prompt_api_key", "", "阿里百炼API Key"),
    ("prompt_system_prompt", "", "提示词生成系统Prompt"),
    # 配置项4：图片生成引擎配置
    ("generate_engine", "seedream", "图片生成引擎(seedream/nanobanana)"),
    ("apiyi_api_key", "", "API易平台统一API Key（SeedDream 4.5 + Nano Banana Pro）"),
    ("generate_model_version", "seedream-4-5-251128", "模型版本"),
    ("disable_generation_watermark", "1", "关闭生图水印(1=关闭/0=保留)"),
    ("strict_no_watermark", "1", "强制无水印校验(1=开启，失败则整张判失败)"),
    ("generate_prompt_prefix", "", "默认提示词前缀"),
    ("generate_prompt_suffix", "", "默认提示词后缀"),
    # 配置项5：宽脸版本生成配置
    ("wideface_engine", "nanobanana", "宽脸生成引擎(nanobanana/seedream)"),
    ("wideface_prompt", "", "宽脸生成提示词"),
    # 配置项6：画质压缩配置
    ("compress_enabled", "1", "启用压缩"),
    ("compress_target_size", "500", "压缩目标文件大小(KB)"),
    ("compress_min_quality", "60", "最低画质(40-80)"),
    ("compress_max_quality", "95", "最高画质(80-100)"),
    # 配置项7：导出设置
    ("export_default_dir", "", "默认导出目录"),
    ("notification_sound", "1", "任务完成后播放提示音"),
    ("notification_browser", "1", "任务完成后浏览器通知"),
]


def seed_default_settings():
    """插入默认系统配置（幂等，已存在的不覆盖）"""
    db = SessionLocal()
    try:
        for key, value, description in DEFAULT_SETTINGS:
            existing = db.query(Settings).filter(Settings.key == key).first()
            if not existing:
                db.add(Settings(key=key, value=value, description=description))
        db.commit()
    finally:
        db.close()
