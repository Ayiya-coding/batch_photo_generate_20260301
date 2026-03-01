"""
Schema 校验测试：验证 Pydantic 模型的 Field 约束和 validator 逻辑
"""
import pytest
from pydantic import ValidationError

from app.schemas.common import (
    UploadRequest,
    PreprocessRequest,
    PromptGenerateRequest,
    GenerateRequest,
    ReviewMarkRequest,
    ReviewBatchMarkRequest,
    WideFaceGenerateRequest,
    WideFaceReviewRequest,
    CompressRequest,
    ExportRequest,
    BatchDownloadRequest,
    SettingItem,
    TestConnectionRequest,
)

VALID_UUID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
BAD_UUID = "not-a-uuid"


# ── UUID 校验 ──────────────────────────────────────────────────

class TestUUIDValidation:
    def test_valid_uuid_accepted(self):
        req = PreprocessRequest(batch_id=VALID_UUID)
        assert req.batch_id == VALID_UUID

    def test_bad_uuid_rejected(self):
        with pytest.raises(ValidationError, match="格式不合法"):
            PreprocessRequest(batch_id=BAD_UUID)

    def test_generate_bad_uuid(self):
        with pytest.raises(ValidationError, match="格式不合法"):
            GenerateRequest(batch_id="12345")

    def test_review_bad_task_id(self):
        with pytest.raises(ValidationError, match="格式不合法"):
            ReviewMarkRequest(task_id="xxx", status="selected")


# ── UploadRequest ──────────────────────────────────────────────

class TestUploadRequest:
    def test_valid(self):
        req = UploadRequest(batch_name="测试批次")
        assert req.batch_name == "测试批次"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            UploadRequest(batch_name="")

    def test_long_name_rejected(self):
        with pytest.raises(ValidationError):
            UploadRequest(batch_name="x" * 201)

    def test_long_description_rejected(self):
        with pytest.raises(ValidationError):
            UploadRequest(batch_name="ok", batch_description="x" * 1001)


# ── PreprocessRequest ──────────────────────────────────────────

class TestPreprocessRequest:
    def test_valid_crop(self):
        req = PreprocessRequest(batch_id=VALID_UUID, mode="crop")
        assert req.mode == "crop"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            PreprocessRequest(batch_id=VALID_UUID, mode="stretch")

    def test_offset_in_range(self):
        req = PreprocessRequest(
            batch_id=VALID_UUID,
            crop_offsets={VALID_UUID: 0.5},
        )
        assert req.crop_offsets[VALID_UUID] == 0.5

    def test_offset_out_of_range(self):
        with pytest.raises(ValidationError, match="超出"):
            PreprocessRequest(
                batch_id=VALID_UUID,
                crop_offsets={VALID_UUID: 1.5},
            )

    def test_offset_bad_key_uuid(self):
        with pytest.raises(ValidationError, match="格式不合法"):
            PreprocessRequest(
                batch_id=VALID_UUID,
                crop_offsets={"bad-key": 0.0},
            )

    def test_expand_offset_in_range(self):
        req = PreprocessRequest(
            batch_id=VALID_UUID,
            expand_offsets={VALID_UUID: -0.25},
        )
        assert req.expand_offsets[VALID_UUID] == -0.25

    def test_expand_offset_out_of_range(self):
        with pytest.raises(ValidationError, match="超出"):
            PreprocessRequest(
                batch_id=VALID_UUID,
                expand_offsets={VALID_UUID: 2.0},
            )


# ── PromptGenerateRequest ─────────────────────────────────────

class TestPromptGenerateRequest:
    def test_valid_crowd_types(self):
        req = PromptGenerateRequest(batch_id=VALID_UUID, crowd_types=["C01", "C02"])
        assert req.crowd_types == ["C01", "C02"]

    def test_invalid_crowd_type(self):
        with pytest.raises(ValidationError, match="无效的人群类型"):
            PromptGenerateRequest(batch_id=VALID_UUID, crowd_types=["C99"])

    def test_none_crowd_types_ok(self):
        req = PromptGenerateRequest(batch_id=VALID_UUID)
        assert req.crowd_types is None

    def test_reference_image_id_valid(self):
        req = PromptGenerateRequest(
            batch_id=VALID_UUID,
            crowd_types=["C01"],
            reference_image_id=VALID_UUID,
        )
        assert req.reference_image_id == VALID_UUID

    def test_reference_image_id_invalid(self):
        with pytest.raises(ValidationError, match="reference_image_id 格式不合法"):
            PromptGenerateRequest(
                batch_id=VALID_UUID,
                crowd_types=["C01"],
                reference_image_id="bad-id",
            )


# ── GenerateRequest ────────────────────────────────────────────

class TestGenerateRequest:
    def test_valid_engine(self):
        req = GenerateRequest(batch_id=VALID_UUID, engine="seedream")
        assert req.engine == "seedream"

    def test_invalid_engine(self):
        with pytest.raises(ValidationError, match="引擎必须为"):
            GenerateRequest(batch_id=VALID_UUID, engine="dalle")

    def test_none_engine_ok(self):
        req = GenerateRequest(batch_id=VALID_UUID)
        assert req.engine is None


# ── ReviewBatchMarkRequest ─────────────────────────────────────

class TestReviewBatchMark:
    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError):
            ReviewBatchMarkRequest(task_ids=[], status="selected")

    def test_bad_status_rejected(self):
        with pytest.raises(ValidationError, match="status 必须为"):
            ReviewBatchMarkRequest(task_ids=[VALID_UUID], status="approved")

    def test_valid(self):
        req = ReviewBatchMarkRequest(task_ids=[VALID_UUID], status="selected")
        assert len(req.task_ids) == 1


# ── WideFaceGenerateRequest ────────────────────────────────────

class TestWideFaceGenerate:
    def test_empty_ids_rejected(self):
        with pytest.raises(ValidationError):
            WideFaceGenerateRequest(template_ids=[])

    def test_bad_id_in_list(self):
        with pytest.raises(ValidationError, match="格式不合法"):
            WideFaceGenerateRequest(template_ids=["bad"])

    def test_valid(self):
        req = WideFaceGenerateRequest(template_ids=[VALID_UUID])
        assert req.template_ids == [VALID_UUID]


# ── CompressRequest ────────────────────────────────────────────

class TestCompressRequest:
    def test_defaults(self):
        req = CompressRequest()
        assert req.target_size_kb == 500
        assert req.min_quality == 60
        assert req.max_quality == 95

    def test_target_too_small(self):
        with pytest.raises(ValidationError):
            CompressRequest(target_size_kb=10)

    def test_target_too_large(self):
        with pytest.raises(ValidationError):
            CompressRequest(target_size_kb=9999)

    def test_min_quality_too_low(self):
        with pytest.raises(ValidationError):
            CompressRequest(min_quality=5)

    def test_max_lte_min_rejected(self):
        with pytest.raises(ValidationError, match="max_quality 必须大于"):
            CompressRequest(min_quality=80, max_quality=80)


# ── ExportRequest ──────────────────────────────────────────────

class TestExportRequest:
    def test_valid_path(self):
        req = ExportRequest(export_dir="/tmp/output")
        assert req.export_dir == "/tmp/output"

    def test_traversal_rejected(self):
        with pytest.raises(ValidationError, match="不允许包含"):
            ExportRequest(export_dir="/tmp/../etc")

    def test_none_ok(self):
        req = ExportRequest()
        assert req.export_dir is None


# ── BatchDownloadRequest ───────────────────────────────────────

class TestBatchDownload:
    def test_valid_crowd(self):
        req = BatchDownloadRequest(crowd_type="C01")
        assert req.crowd_type == "C01"

    def test_invalid_crowd(self):
        with pytest.raises(ValidationError, match="无效的人群类型"):
            BatchDownloadRequest(crowd_type="X99")


# ── SettingItem ────────────────────────────────────────────────

class TestSettingItem:
    def test_valid(self):
        item = SettingItem(key="api_key", value="abc123")
        assert item.key == "api_key"

    def test_key_with_special_chars(self):
        with pytest.raises(ValidationError):
            SettingItem(key="bad key!", value="v")

    def test_empty_key(self):
        with pytest.raises(ValidationError):
            SettingItem(key="", value="v")


# ── TestConnectionRequest ─────────────────────────────────────

class TestConnectionRequestSchema:
    def test_valid(self):
        req = TestConnectionRequest(service="bailian", api_key="sk-xxx")
        assert req.service == "bailian"

    def test_invalid_service(self):
        with pytest.raises(ValidationError):
            TestConnectionRequest(service="openai", api_key="sk-xxx")

    def test_empty_api_key(self):
        with pytest.raises(ValidationError):
            TestConnectionRequest(service="bailian", api_key="")
