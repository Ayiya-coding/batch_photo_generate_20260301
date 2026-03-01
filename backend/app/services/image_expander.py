"""
图片扩图服务 - 将图片扩展到目标比例 (默认 9:16)

支持引擎:
1. IOPaint outpainting (本地服务, 默认)
2. API易平台 SeedDream 4.5 outpainting (云端API)
3. OpenCV 边缘填充 (降级方案)
"""
import cv2
import numpy as np
import httpx
import base64
from pathlib import Path
from typing import Optional
import logging

from app.services.watermark_remover import IOPaintClient
from app.core.config import settings as app_settings

logger = logging.getLogger(__name__)


def _compute_padding(
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    offset: float = 0.0,
) -> tuple[int, int, int, int]:
    """根据目标尺寸与偏移计算 padding(top, bottom, left, right)。"""
    clamped = max(-1.0, min(1.0, float(offset)))
    total_h = max(0, target_h - src_h)
    total_w = max(0, target_w - src_w)

    pad_top = int(round(total_h * (0.5 + clamped * 0.5)))
    pad_top = max(0, min(total_h, pad_top))
    pad_bottom = total_h - pad_top

    pad_left = int(round(total_w * (0.5 + clamped * 0.5)))
    pad_left = max(0, min(total_w, pad_left))
    pad_right = total_w - pad_left
    return pad_top, pad_bottom, pad_left, pad_right


def _smooth_seam_band(image: np.ndarray, y0: int, y1: int, x0: int, x1: int):
    """对拼接带做轻度平滑，减少撕裂条纹。"""
    h, w = image.shape[:2]
    ys = max(0, y0)
    ye = min(h, y1)
    xs = max(0, x0)
    xe = min(w, x1)
    if ys >= ye or xs >= xe:
        return

    band = image[ys:ye, xs:xe]
    if band.size == 0:
        return
    image[ys:ye, xs:xe] = cv2.GaussianBlur(band, (0, 0), sigmaX=1.1, sigmaY=1.1)


def _feather_blend_source_patch(
    result_image: np.ndarray,
    source_patch: np.ndarray,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    feather: int,
):
    """
    将原图 patch 以羽化方式贴回扩图结果，降低硬边和锯齿缝。
    """
    patch_h = y1 - y0
    patch_w = x1 - x0
    if patch_h <= 0 or patch_w <= 0:
        return

    # 1) 基础 alpha: 源 patch 区域=1
    alpha = np.zeros(result_image.shape[:2], dtype=np.float32)
    alpha[y0:y1, x0:x1] = 1.0
    alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=max(1.0, feather * 0.45), sigmaY=max(1.0, feather * 0.45))

    # 2) 强制中心区域完全使用原图，避免主体被污染
    core_margin = min(max(4, feather * 2), max(4, min(patch_h, patch_w) // 4))
    cy0 = min(y1, y0 + core_margin)
    cy1 = max(y0, y1 - core_margin)
    cx0 = min(x1, x0 + core_margin)
    cx1 = max(x0, x1 - core_margin)
    if cy0 < cy1 and cx0 < cx1:
        alpha[cy0:cy1, cx0:cx1] = 1.0

    overlay = np.zeros_like(result_image)
    overlay[y0:y1, x0:x1] = source_patch

    a3 = alpha[..., None]
    blended = overlay.astype(np.float32) * a3 + result_image.astype(np.float32) * (1.0 - a3)
    np.copyto(result_image, blended.astype(np.uint8))


def _smooth_expanded_regions(
    image: np.ndarray,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
):
    """
    对扩展区做轻量去条纹与抗锯齿处理，不触碰主体区域。
    """
    h, w = image.shape[:2]

    def _blend_region(region: np.ndarray, kx: int, ky: int, alpha: float):
        if region.size == 0:
            return region
        blur = cv2.GaussianBlur(region, (kx, ky), 0)
        out = cv2.addWeighted(region, 1.0 - alpha, blur, alpha, 0)
        return out

    # 顶部扩展区
    if y0 > 0:
        top = image[:y0, :]
        image[:y0, :] = _blend_region(top, 15, 3, 0.28)
    # 底部扩展区
    if y1 < h:
        bottom = image[y1:, :]
        image[y1:, :] = _blend_region(bottom, 15, 3, 0.28)
    # 左侧扩展区
    if x0 > 0:
        left = image[:, :x0]
        image[:, :x0] = _blend_region(left, 3, 15, 0.22)
    # 右侧扩展区
    if x1 < w:
        right = image[:, x1:]
        image[:, x1:] = _blend_region(right, 3, 15, 0.22)


def _postprocess_outpaint_result(
    result_image: np.ndarray,
    source_image: np.ndarray,
    target_width: int,
    target_height: int,
    offset: float = 0.0,
) -> np.ndarray:
    """
    AI 扩图后处理：
    1) 尺寸对齐
    2) 回贴原图中心区域，避免主区域被撕裂
    3) 拼接缝轻度平滑，降低上下条纹
    """
    if result_image is None:
        return source_image

    result_h, result_w = result_image.shape[:2]
    if (result_w, result_h) != (target_width, target_height):
        result_image = cv2.resize(result_image, (target_width, target_height), interpolation=cv2.INTER_CUBIC)

    src_h, src_w = source_image.shape[:2]
    pad_top, pad_bottom, pad_left, pad_right = _compute_padding(
        src_w, src_h, target_width, target_height, offset=offset,
    )

    y0 = pad_top
    y1 = min(target_height, y0 + src_h)
    x0 = pad_left
    x1 = min(target_width, x0 + src_w)

    src_crop = source_image[: y1 - y0, : x1 - x0]
    seam = max(4, min(22, min(src_h, src_w) // 28))

    # 先对扩展区做去条纹，再羽化回贴源图
    _smooth_expanded_regions(result_image, y0, y1, x0, x1)
    _feather_blend_source_patch(
        result_image=result_image,
        source_patch=src_crop,
        y0=y0,
        y1=y1,
        x0=x0,
        x1=x1,
        feather=seam,
    )

    if pad_top > 0:
        _smooth_seam_band(result_image, y0 - seam, y0 + seam, x0, x1)
    if pad_bottom > 0:
        _smooth_seam_band(result_image, y1 - seam, y1 + seam, x0, x1)
    if pad_left > 0:
        _smooth_seam_band(result_image, y0, y1, x0 - seam, x0 + seam)
    if pad_right > 0:
        _smooth_seam_band(result_image, y0, y1, x1 - seam, x1 + seam)

    return result_image


class APIYiOutpaintClient:
    """API易平台 outpainting 客户端 (SeedDream 4.5)"""

    def __init__(self, api_key: str = "", api_url: str = "https://api.apiyi.com"):
        self.api_key = api_key or app_settings.APIYI_API_KEY
        self.api_url = api_url.rstrip("/")
        self.timeout = 120.0

    async def outpaint(
        self,
        image: np.ndarray,
        target_width: int,
        target_height: int,
    ) -> Optional[np.ndarray]:
        """
        调用 API易平台 SeedDream outpainting

        Args:
            image: BGR 格式输入图片
            target_width: 目标宽度
            target_height: 目标高度

        Returns:
            扩图后的 BGR 图片, 失败返回 None
        """
        if not self.api_key:
            logger.warning("API易 API Key 未配置，跳过云端扩图")
            return None

        # 编码图片为 base64
        _, img_buf = cv2.imencode(".png", image)
        img_b64 = base64.b64encode(img_buf.tobytes()).decode("utf-8")

        h, w = image.shape[:2]

        payload = {
            "model": "seedream-4.5",
            "input": {
                "image": img_b64,
                "function": "outpainting",
                "output_image_ratio": f"{target_width}:{target_height}",
            },
            "parameters": {
                "n": 1,
            }
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.api_url}/v1/images/generations",
                    json=payload,
                    headers=headers,
                )

                if resp.status_code != 200:
                    logger.error(f"API易 outpaint 失败: HTTP {resp.status_code} - {resp.text[:300]}")
                    return None

                data = resp.json()

                # 解析响应中的图片
                results = data.get("output", {}).get("results", [])
                if not results:
                    results = data.get("data", [])

                if not results:
                    logger.error(f"API易 outpaint 返回空结果: {data}")
                    return None

                # 获取第一张结果图片 (base64 或 URL)
                result_item = results[0]
                if isinstance(result_item, dict):
                    img_data = result_item.get("b64_image") or result_item.get("b64_json", "")
                    img_url = result_item.get("url", "")
                else:
                    img_data = ""
                    img_url = str(result_item)

                if img_data:
                    img_bytes = base64.b64decode(img_data)
                elif img_url:
                    # 下载图片
                    async with httpx.AsyncClient(timeout=30.0) as dl_client:
                        dl_resp = await dl_client.get(img_url)
                        img_bytes = dl_resp.content
                else:
                    logger.error("API易 outpaint 返回无图片数据")
                    return None

                result_array = np.frombuffer(img_bytes, dtype=np.uint8)
                result_image = cv2.imdecode(result_array, cv2.IMREAD_COLOR)

                if result_image is None:
                    logger.error("无法解码 API易 outpaint 返回的图片")
                    return None

                logger.info(f"API易 outpaint 成功: {w}x{h} -> {target_width}x{target_height}")
                return result_image

        except httpx.RequestError as e:
            logger.error(f"API易 outpaint 网络错误: {e}")
            return None
        except Exception as e:
            logger.error(f"API易 outpaint 异常: {e}")
            return None


def crop_to_target_ratio(
    input_path: str,
    output_path: str,
    target_ratio: tuple = (9, 16),
    offset: float = 0.0,
) -> bool:
    """
    将图片裁剪到目标宽高比 (纯 OpenCV，无需外部服务)

    Args:
        input_path: 输入图片路径
        output_path: 输出图片路径
        target_ratio: 目标宽高比 (width, height), 如 (9, 16)
        offset: 裁剪偏移量 -1.0 ~ 1.0, 0 = 居中

    Returns:
        是否成功
    """
    try:
        image = cv2.imread(input_path)
        if image is None:
            logger.error(f"无法读取图片: {input_path}")
            return False

        h, w = image.shape[:2]
        target_w, target_h = target_ratio
        current_ratio = w / h
        target_ratio_val = target_w / target_h

        # 如果已接近目标比例 (±5%), 直接复制
        tolerance = 0.05 * target_ratio_val
        if abs(current_ratio - target_ratio_val) < tolerance:
            logger.info(f"图片比例已接近 {target_w}:{target_h}, 跳过裁剪")
            if str(Path(input_path).resolve()) != str(Path(output_path).resolve()):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(output_path, image)
            return True

        # 计算裁剪区域
        if current_ratio > target_ratio_val:
            # 图片更宽 → 左右裁剪，保留完整高度
            new_w = int(h * target_ratio_val)
            new_h = h
            max_offset = (w - new_w) // 2
            cx = w // 2 + int(offset * max_offset)
            x1 = max(0, cx - new_w // 2)
            x1 = min(x1, w - new_w)
            y1 = 0
        else:
            # 图片更高 → 上下裁剪，保留完整宽度
            new_w = w
            new_h = int(w / target_ratio_val)
            max_offset = (h - new_h) // 2
            cy = h // 2 + int(offset * max_offset)
            y1 = max(0, cy - new_h // 2)
            y1 = min(y1, h - new_h)
            x1 = 0

        cropped = image[y1:y1 + new_h, x1:x1 + new_w]

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(output_path, cropped)

        logger.info(f"裁剪成功: {w}x{h} -> {new_w}x{new_h} (offset={offset:.2f})")
        return True

    except Exception as e:
        logger.error(f"裁剪失败: {input_path}, 错误: {e}")
        return False


async def expand_to_target_ratio(
    input_path: str,
    output_path: str,
    target_ratio: tuple = (9, 16),
    engine: str = "auto",
    iopaint_url: str = app_settings.IOPAINT_URL,
    apiyi_api_key: str = "",
    offset: float = 0.0,
    allow_fallback: bool = True,
) -> bool:
    """
    将图片扩展到目标宽高比

    Args:
        input_path: 输入图片路径
        output_path: 输出图片路径
        target_ratio: 目标宽高比 (width, height), 如 (9, 16)
        engine: 扩图引擎 "auto" / "iopaint" / "seedream"
                auto: 优先 IOPaint, 不可用则尝试 API易, 最后 OpenCV 降级
        iopaint_url: IOPaint 服务地址
        offset: 扩图偏移量 [-1, 1]，0=居中；正值表示上/左扩展更多

    Returns:
        是否成功
    """
    try:
        image = cv2.imread(input_path)
        if image is None:
            logger.error(f"无法读取图片: {input_path}")
            return False

        h, w = image.shape[:2]
        target_w, target_h = target_ratio
        current_ratio = w / h
        target_ratio_val = target_w / target_h

        # 如果已接近目标比例 (±5%), 跳过
        tolerance = 0.05 * target_ratio_val
        if abs(current_ratio - target_ratio_val) < tolerance:
            logger.info(
                f"图片比例 {w}:{h} ({current_ratio:.3f}) "
                f"已接近目标 {target_w}:{target_h} ({target_ratio_val:.3f}), 跳过扩图"
            )
            if str(Path(input_path).resolve()) != str(Path(output_path).resolve()):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(output_path, image)
            return True

        # 计算目标尺寸 (保留较大维度，扩展较小维度)
        if current_ratio > target_ratio_val:
            new_w = w
            new_h = int(w / target_ratio_val)
        else:
            new_h = h
            new_w = int(h * target_ratio_val)

        logger.info(f"扩图: {w}x{h} -> {new_w}x{new_h} (目标比例 {target_w}:{target_h}, 引擎={engine})")

        result_image = None
        result_source = "none"

        # 引擎选择
        if engine in ("auto", "iopaint"):
            result_image = await _try_iopaint(image, new_w, new_h, iopaint_url, offset=offset)
            if result_image is not None:
                result_source = "iopaint"

        if result_image is None and engine in ("auto", "seedream"):
            result_image = await _try_apiyi(image, new_w, new_h, apiyi_api_key)
            if result_image is not None:
                result_source = "seedream"

        if result_image is not None:
            # Seedream 不支持 offset，避免错误位移，按居中进行回贴
            repair_offset = offset if result_source == "iopaint" else 0.0
            result_image = _postprocess_outpaint_result(
                result_image=result_image,
                source_image=image,
                target_width=new_w,
                target_height=new_h,
                offset=repair_offset,
            )
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(output_path, result_image)
            logger.info(f"扩图成功: {input_path} -> {output_path} ({new_w}x{new_h})")
            return True

        # 所有AI引擎都失败
        if not allow_fallback:
            logger.error("AI扩图失败且已禁用降级方案")
            return False

        logger.warning("所有AI扩图引擎不可用，使用 OpenCV 边缘填充降级")
        return _fallback_expand(image, output_path, new_w, new_h, offset=offset)

    except Exception as e:
        logger.error(f"扩图失败: {input_path}, 错误: {e}")
        return False


async def _try_iopaint(
    image: np.ndarray,
    target_width: int,
    target_height: int,
    iopaint_url: str,
    offset: float = 0.0,
) -> Optional[np.ndarray]:
    """尝试使用 IOPaint outpainting"""
    client = IOPaintClient(service_url=iopaint_url)
    try:
        return await client.outpaint(
            image,
            target_width=target_width,
            target_height=target_height,
            offset=offset,
        )
    except ConnectionError:
        logger.info("IOPaint 服务不可用")
        return None
    except Exception as e:
        logger.warning(f"IOPaint outpaint 失败: {e}")
        return None
    finally:
        await client.close()


async def _try_apiyi(
    image: np.ndarray, target_width: int, target_height: int, api_key: str = ""
) -> Optional[np.ndarray]:
    """尝试使用 API易平台 SeedDream outpainting"""
    client = APIYiOutpaintClient(api_key=api_key)
    return await client.outpaint(image, target_width, target_height)


def _fallback_expand(
    image: np.ndarray,
    output_path: str,
    target_width: int,
    target_height: int,
    offset: float = 0.0,
) -> bool:
    """
    降级方案: 当所有AI引擎不可用时，使用 OpenCV 边缘复制填充
    """
    try:
        h, w = image.shape[:2]

        # 正 offset 表示原图向下/右偏移，即上/左扩展更多
        clamped = max(-1.0, min(1.0, float(offset)))

        total_h = max(0, target_height - h)
        total_w = max(0, target_width - w)

        pad_top = int(round(total_h * (0.5 + clamped * 0.5)))
        pad_top = max(0, min(total_h, pad_top))
        pad_bottom = total_h - pad_top

        pad_left = int(round(total_w * (0.5 + clamped * 0.5)))
        pad_left = max(0, min(total_w, pad_left))
        pad_right = total_w - pad_left

        result = cv2.copyMakeBorder(
            image,
            pad_top, pad_bottom, pad_left, pad_right,
            cv2.BORDER_REPLICATE,
        )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(output_path, result)

        logger.info(
            f"降级扩图完成: {w}x{h} -> {target_width}x{target_height} "
            f"(offset={clamped:.2f}, top={pad_top}, bottom={pad_bottom}, left={pad_left}, right={pad_right})"
        )
        return True

    except Exception as e:
        logger.error(f"降级扩图失败: {e}")
        return False
