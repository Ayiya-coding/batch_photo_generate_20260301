"""
API 请求/响应数据模型
"""
import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Literal
from datetime import datetime

# UUID v4 格式校验
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)

# 合法人群类型 ID
_VALID_CROWD_IDS = {f"C{i:02d}" for i in range(1, 20)}

# 合法引擎名
_VALID_ENGINES = {"seedream", "nanobanana"}


def _check_uuid(v: str, label: str = "ID") -> str:
    if not _UUID_RE.match(v):
        raise ValueError(f"{label} 格式不合法")
    return v


# ===== 上传相关 =====
class UploadRequest(BaseModel):
    """图片上传请求"""
    batch_name: str = Field(..., min_length=1, max_length=200, description="批次名称")
    batch_description: Optional[str] = Field(None, max_length=1000, description="批次描述")


class UploadResponse(BaseModel):
    """上传响应"""
    batch_id: str
    uploaded_count: int
    failed_count: int
    message: str


# ===== 预处理相关 =====
class PreprocessRequest(BaseModel):
    """预处理请求"""
    batch_id: str
    mode: Literal["crop", "expand"] = Field("crop", description="默认处理模式（兼容旧调用）")
    image_modes: Optional[Dict[str, str]] = Field(None, description="每张图的模式 {image_id: 'crop'|'expand'}，优先于 mode")
    crop_offsets: Optional[Dict[str, float]] = Field(None, description="每张图的裁剪偏移量 {image_id: offset}，范围 -1~1，0=居中")
    expand_offsets: Optional[Dict[str, float]] = Field(None, description="每张图的扩图偏移量 {image_id: offset}，范围 -1~1，0=居中")

    @field_validator("batch_id")
    @classmethod
    def _batch_id(cls, v: str) -> str:
        return _check_uuid(v, "batch_id")

    @field_validator("image_modes")
    @classmethod
    def _image_modes(cls, v: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        if v is None:
            return v
        allowed = {"crop", "expand"}
        for key, val in v.items():
            _check_uuid(key, "image_modes key")
            if val not in allowed:
                raise ValueError(f"image_modes 值 '{val}' 不合法，仅允许 {allowed}")
        return v

    @field_validator("crop_offsets")
    @classmethod
    def _offsets(cls, v: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if v is None:
            return v
        for key, val in v.items():
            _check_uuid(key, "crop_offsets key")
            if not -1.0 <= val <= 1.0:
                raise ValueError(f"偏移量 {val} 超出 [-1, 1] 范围")
        return v

    @field_validator("expand_offsets")
    @classmethod
    def _expand_offsets(cls, v: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if v is None:
            return v
        for key, val in v.items():
            _check_uuid(key, "expand_offsets key")
            if not -1.0 <= val <= 1.0:
                raise ValueError(f"扩图偏移量 {val} 超出 [-1, 1] 范围")
        return v


class PreprocessResponse(BaseModel):
    """预处理响应"""
    processed_count: int
    failed_count: int
    message: str


class WatermarkMarkRequest(BaseModel):
    """水印手动涂抹标记请求"""
    image_id: str
    mask_data: str = Field(..., min_length=1, max_length=20_000_000, description="Base64编码的涂抹蒙版图片")

    @field_validator("image_id")
    @classmethod
    def _image_id(cls, v: str) -> str:
        return _check_uuid(v, "image_id")


# ===== 提示词相关 =====
class PromptGenerateRequest(BaseModel):
    """提示词生成请求"""
    batch_id: str
    crowd_types: Optional[List[str]] = Field(None, description="指定人群类型，不指定则全部")
    base_image_id: Optional[str] = Field(None, description="指定底图ID，不指定则为所有底图生成")

    @field_validator("batch_id")
    @classmethod
    def _batch_id(cls, v: str) -> str:
        return _check_uuid(v, "batch_id")

    @field_validator("base_image_id")
    @classmethod
    def _base_image_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _check_uuid(v, "base_image_id")
        return v

    @field_validator("crowd_types")
    @classmethod
    def _crowd_types(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        bad = [c for c in v if c not in _VALID_CROWD_IDS]
        if bad:
            raise ValueError(f"无效的人群类型: {bad}")
        return v


class PromptResponse(BaseModel):
    """提示词响应"""
    batch_id: str
    total_prompts: int
    message: str


# ===== 生图相关 =====
class GenerateRequest(BaseModel):
    """生图请求"""
    batch_id: str
    engine: Optional[str] = Field(None, description="AI引擎: seedream 或 nanobanana")

    @field_validator("batch_id")
    @classmethod
    def _batch_id(cls, v: str) -> str:
        return _check_uuid(v, "batch_id")

    @field_validator("engine")
    @classmethod
    def _engine(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_ENGINES:
            raise ValueError(f"引擎必须为 {_VALID_ENGINES} 之一")
        return v


class GenerateResponse(BaseModel):
    """生图响应"""
    task_id: str
    total_tasks: int
    message: str


# ===== 审核相关 =====
_REVIEW_STATUSES = {"selected", "pending_modification", "not_selected"}


class ReviewMarkRequest(BaseModel):
    """图片审核标记请求"""
    task_id: str
    status: str = Field(..., description="selected/pending_modification/not_selected")

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, v: str) -> str:
        return _check_uuid(v, "task_id")

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in _REVIEW_STATUSES:
            raise ValueError(f"status 必须为 {_REVIEW_STATUSES} 之一")
        return v


class ReviewBatchMarkRequest(BaseModel):
    """批量审核标记请求"""
    task_ids: List[str] = Field(..., min_length=1, max_length=500)
    status: str

    @field_validator("task_ids")
    @classmethod
    def _task_ids(cls, v: List[str]) -> List[str]:
        for tid in v:
            _check_uuid(tid, "task_ids 元素")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in _REVIEW_STATUSES:
            raise ValueError(f"status 必须为 {_REVIEW_STATUSES} 之一")
        return v


# ===== 模板图管理 =====
class TemplateUpdateRequest(BaseModel):
    """模板图更新请求"""
    template_id: str
    new_image_path: Optional[str] = None
    action: Literal["replace", "move_to_pending", "move_to_trash"] = Field(..., description="操作类型")

    @field_validator("template_id")
    @classmethod
    def _template_id(cls, v: str) -> str:
        return _check_uuid(v, "template_id")


class TemplateMoveRequest(BaseModel):
    """模板图库间移动请求"""
    template_id: str
    target: Literal["selected", "pending_modification", "trash"] = Field(..., description="目标库")

    @field_validator("template_id")
    @classmethod
    def _template_id(cls, v: str) -> str:
        return _check_uuid(v, "template_id")


class BatchDownloadRequest(BaseModel):
    """批量下载请求"""
    crowd_type: str
    export_dir: Optional[str] = Field(None, max_length=500)

    @field_validator("crowd_type")
    @classmethod
    def _crowd_type(cls, v: str) -> str:
        if v not in _VALID_CROWD_IDS:
            raise ValueError(f"无效的人群类型: {v}")
        return v


# ===== 宽脸图相关 =====
class WideFaceGenerateRequest(BaseModel):
    """宽脸图生成请求"""
    template_ids: List[str] = Field(..., min_length=1, max_length=500, description="模板ID列表")
    engine: Optional[str] = Field(None, description="生成引擎: seedream 或 nanobanana")

    @field_validator("template_ids")
    @classmethod
    def _template_ids(cls, v: List[str]) -> List[str]:
        for tid in v:
            _check_uuid(tid, "template_ids 元素")
        return v

    @field_validator("engine")
    @classmethod
    def _engine(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_ENGINES:
            raise ValueError(f"引擎必须为 {_VALID_ENGINES} 之一")
        return v


class WideFaceReviewRequest(BaseModel):
    """宽脸图审核请求"""
    template_id: str
    status: Literal["pass", "regenerate"] = Field(..., description="审核结果")

    @field_validator("template_id")
    @classmethod
    def _template_id(cls, v: str) -> str:
        return _check_uuid(v, "template_id")


# ===== 画质压缩 =====
class CompressRequest(BaseModel):
    """画质压缩请求"""
    target_size_kb: Optional[int] = Field(500, ge=50, le=5000, description="目标文件大小（KB）")
    min_quality: Optional[int] = Field(60, ge=10, le=95, description="最低画质 10-95")
    max_quality: Optional[int] = Field(95, ge=50, le=100, description="最高画质 50-100")

    @field_validator("max_quality")
    @classmethod
    def _quality_range(cls, v: Optional[int], info) -> Optional[int]:
        min_q = info.data.get("min_quality")
        if v is not None and min_q is not None and v <= min_q:
            raise ValueError("max_quality 必须大于 min_quality")
        return v


# ===== 批量导出 =====
class ExportRequest(BaseModel):
    """批量导出请求"""
    export_dir: Optional[str] = Field(None, max_length=500, description="导出目录")

    @field_validator("export_dir")
    @classmethod
    def _no_traversal(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and ".." in v:
            raise ValueError("路径不允许包含 '..'")
        return v


# ===== 系统设置 =====
class SettingItem(BaseModel):
    """单个设置项"""
    key: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_]+$")
    value: str = Field(..., max_length=10000)


class SettingBatchUpdateRequest(BaseModel):
    """批量更新设置请求"""
    settings: List[SettingItem] = Field(..., min_length=1, max_length=50)


class TestConnectionRequest(BaseModel):
    """API 连接测试请求"""
    service: Literal["bailian", "apiyi"] = Field(..., description="服务名称")
    api_key: str = Field(..., min_length=1, max_length=500, description="API Key")


# ===== 通用响应 =====
class BaseResponse(BaseModel):
    """基础响应"""
    code: int = Field(0, description="错误码，0为成功")
    message: str = Field("成功", description="响应消息")
    data: Optional[dict] = None


class ErrorResponse(BaseModel):
    """错误响应"""
    code: int
    message: str
    detail: Optional[str] = None
