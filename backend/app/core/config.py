"""
系统核心配置
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    """系统配置类"""

    # 应用配置
    APP_NAME: str = "AI图片批量生成系统"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 数据库配置
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/database.db"

    # 文件存储路径
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    PROCESSED_DIR: Path = DATA_DIR / "processed"
    GENERATED_DIR: Path = DATA_DIR / "generated"
    SELECTED_DIR: Path = DATA_DIR / "selected"
    PENDING_DIR: Path = DATA_DIR / "pending"
    COMPRESSED_DIR: Path = DATA_DIR / "compressed"
    TRASH_DIR: Path = DATA_DIR / "trash"
    LOGS_DIR: Path = DATA_DIR / "logs"

    # AI模型路径
    MODELS_DIR: Path = BASE_DIR / "models"
    LAMA_MODEL_PATH: Path = MODELS_DIR / "lama"

    # AI服务API配置
    # API易平台统一密钥（SeedDream 4.5 + Nano Banana Pro 共用）
    APIYI_API_KEY: str = ""
    APIYI_API_URL: str = "https://api.apiyi.com"

    # 阿里百炼API（提示词生成）
    BAILIAN_API_KEY: str = ""
    BAILIAN_API_URL: str = "https://dashscope.aliyuncs.com/api/v1"

    # 生图引擎选择 (seedream / nanobanana)
    IMAGE_GENERATION_ENGINE: str = "seedream"

    # 宽脸图生成引擎选择 (seedream / nanobanana)
    WIDEFACE_GENERATION_ENGINE: str = "nanobanana"

    # IOPaint 服务地址（Docker 内通过环境变量覆盖为 http://iopaint:8090）
    IOPAINT_URL: str = "http://localhost:8090"

    # Volcengine 视觉服务（去水印）
    VOLC_ACCESS_KEY_ID: str = ""
    VOLC_SECRET_ACCESS_KEY: str = ""
    VOLC_REGION: str = "cn-north-1"
    VOLC_SERVICE: str = "cv"

    # 提示词生成系统Prompt
    PROMPT_SYSTEM_PROMPT: str = """你是一个专业的AI图片生成提示词专家。
请根据用户提供的风格模板（风格描述、服饰要点、场景氛围），生成高质量的提示词。
每个提示词应包含：
1. 人物造型描述（服装、发型、气质）
2. 场景融合描述
3. 画面氛围描述
4. 技术参数（高质量、4K、自然光等）
"""

    # 宽脸图生成系统提示词
    WIDEFACE_SYSTEM_PROMPT: str = """基于原图生成宽脸版本，保持人物其他特征不变，
仅将面部宽度增加约15-20%，确保自然过渡。"""

    # 上传限制
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB
    MAX_BATCH_SIZE: int = 100  # 单次最多100张
    ALLOWED_EXTENSIONS: set = {".jpg", ".jpeg", ".png", ".webp"}

    # 并发配置
    MAX_CONCURRENT_TASKS: int = 10

    # 导出配置（Docker 中通过环境变量覆盖为 /app/data/exports）
    DEFAULT_EXPORT_DIR: Path = Path.home() / "Pictures" / "AI图片导出"
    EXPORT_DIR: Path = DATA_DIR / "exports"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

# 创建全局配置实例
settings = Settings()

# 确保所有目录存在
for directory in [
    settings.DATA_DIR,
    settings.UPLOAD_DIR,
    settings.PROCESSED_DIR,
    settings.GENERATED_DIR,
    settings.SELECTED_DIR,
    settings.PENDING_DIR,
    settings.COMPRESSED_DIR,
    settings.TRASH_DIR,
    settings.LOGS_DIR,
    settings.MODELS_DIR,
    settings.EXPORT_DIR,
]:
    directory.mkdir(parents=True, exist_ok=True)
