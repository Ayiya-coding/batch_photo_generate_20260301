"""
图片生成服务 - 对接 API易平台 (SeedDream 4.5 / Nano Banana Pro)

支持:
- SeedDream 4.5: 高质量人物写真生成
- Nano Banana Pro: 快速风格化生成
- 智能并发控制 + 失败重试
"""
import asyncio
import base64
import logging
from pathlib import Path
from typing import Optional

import cv2
import httpx

from app.core.config import settings as app_settings

logger = logging.getLogger(__name__)


def _normalize_watermark_engine(engine_name: str) -> str:
    engine = (engine_name or "auto").strip().lower()
    alias = {
        "volcengine": "volc",
        "volcano": "volc",
        "local": "iopaint",
    }
    engine = alias.get(engine, engine)
    if engine in ("auto", "iopaint", "volc", "opencv"):
        return engine
    return "auto"


class APIYiImageClient:
    """API易平台图片生成客户端"""

    def __init__(
        self,
        api_key: str = "",
        api_url: str = "",
        seedream_model: str = "",
        nanobanana_model: str = "nano-banana-pro",
        disable_watermark: bool = True,
    ):
        self.api_key = api_key or app_settings.APIYI_API_KEY
        self.api_url = (api_url or app_settings.APIYI_API_URL).rstrip("/")
        self.timeout = 240.0  # 单图超时240秒
        self.seedream_model = (seedream_model or "seedream-4-5-251128").strip()
        self.nanobanana_model = (nanobanana_model or "nano-banana-pro").strip()
        self.seedream_size = "1440x2560"  # Seedream 当前渠道要求 >= 3686400 像素，9:16 最小可用
        self.nanobanana_size = "576x1024"
        self.disable_watermark = disable_watermark
        self.last_error_code = ""
        self.last_error_message = ""

    @staticmethod
    def _encode_reference_image(path: str, max_side: int = 896, max_bytes: int = 220 * 1024) -> str:
        """
        读取并压缩参考图，避免请求体过大导致上游解析失败。
        """
        if not path or not Path(path).exists():
            return ""
        image = cv2.imread(path)
        if image is None:
            return ""

        h, w = image.shape[:2]
        long_side = max(h, w)
        if long_side > max_side:
            scale = max_side / float(long_side)
            image = cv2.resize(
                image,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )

        selected = None
        for quality in (86, 78, 70, 62):
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                continue
            selected = buf
            if len(buf) <= max_bytes:
                break
        if selected is None:
            return ""
        return base64.b64encode(selected.tobytes()).decode("utf-8")

    @classmethod
    def _encode_reference_variants(cls, path: str) -> list[str]:
        """
        生成多级压缩版本，遇到上游 invalid_image_request / buffer full 时自动降级重试。
        """
        specs = [
            (960, 260 * 1024),
            (768, 170 * 1024),
            (640, 110 * 1024),
            (512, 72 * 1024),
        ]
        variants: list[str] = []
        for max_side, max_bytes in specs:
            b64 = cls._encode_reference_image(path, max_side=max_side, max_bytes=max_bytes)
            if b64 and b64 not in variants:
                variants.append(b64)
        return variants

    def _reset_last_error(self):
        self.last_error_code = ""
        self.last_error_message = ""

    def _record_error(self, status_code: int, message: str):
        self.last_error_message = (message or "")[:500]
        text = (message or "").lower()

        if (
            "insufficient_user_quota" in text
            or "quota" in text and "not enough" in text
            or "余额不足" in message
        ):
            self.last_error_code = "insufficient_user_quota"
        elif (
            "invalid_image_request" in text
            or "multipart: nextpart" in text
            or "bufio: buffer full" in text
        ):
            self.last_error_code = "invalid_image_request"
        elif "无可用渠道" in message or "no available channel" in text:
            self.last_error_code = "no_available_channel"
        elif status_code == 401:
            self.last_error_code = "unauthorized"
        elif status_code == 429:
            self.last_error_code = "rate_limited"
        elif status_code >= 500:
            self.last_error_code = "upstream_server_error"
        elif status_code == 0:
            self.last_error_code = "request_error"
        else:
            self.last_error_code = f"http_{status_code}"

    def _can_fallback_from_seedream(self) -> bool:
        return self.last_error_code in {
            "insufficient_user_quota",
            "no_available_channel",
            "rate_limited",
            "upstream_server_error",
            "request_error",
            "invalid_image_request",
        }

    def _apply_watermark_options(self, payload: dict):
        """
        按平台兼容格式添加去水印参数：
        - Seedream: watermark=false
        - Drawing API: logo_info.add_logo=false
        """
        if not self.disable_watermark:
            return

        payload["watermark"] = False
        payload["logo_info"] = {
            "add_logo": False,
            "position": 0,
            "language": 0,
            "opacity": 0.3,
        }

    async def generate_image(
        self,
        engine: str,
        prompt: str,
        negative_prompt: str = "",
        reference_image_path: str = "",
        reference_weight: int = 80,
        strict_reference: bool = True,
        output_path: str = "",
    ) -> bool:
        """
        生成单张图片

        Args:
            engine: "seedream" 或 "nanobanana"
            prompt: 正向提示词
            negative_prompt: 负向提示词
            reference_image_path: 参考底图路径（用于风格参考）
            reference_weight: 参考图权重 0-100
            output_path: 输出图片路径

        Returns:
            是否成功
        """
        if not self.api_key:
            raise ValueError("API易 API Key 未配置")
        self._reset_last_error()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 读取参考图片（多级压缩版本，兼容上游请求体限制）
        ref_variants = self._encode_reference_variants(reference_image_path) if reference_image_path else []
        if not ref_variants:
            ref_variants = [""]

        if strict_reference and not ref_variants[0]:
            logger.error("严格参考模式缺少有效参考图: %s", reference_image_path or "<empty>")
            self._record_error(0, "missing_reference_image")
            return False

        if engine == "seedream":
            # 先用 Seedream 逐级尝试参考图尺寸
            for idx, ref_b64 in enumerate(ref_variants, start=1):
                ok = await self._generate_seedream(
                    headers, prompt, negative_prompt, ref_b64, reference_weight, output_path
                )
                if ok:
                    return True

                if ref_b64 and self.last_error_code == "invalid_image_request" and idx < len(ref_variants):
                    logger.warning(
                        "Seedream 参考图请求过大，降级重试第 %s/%s 档压缩图",
                        idx + 1,
                        len(ref_variants),
                    )
                    continue
                break

            # Seedream 渠道/配额异常时自动降级到 NanoBanana，避免整批任务 0 产出
            if self._can_fallback_from_seedream():
                logger.warning(
                    "Seedream 不可用（%s），自动降级到 %s（strict_reference=%s）",
                    self.last_error_code or "unknown",
                    self.nanobanana_model,
                    strict_reference,
                )
                for idx, ref_b64 in enumerate(ref_variants, start=1):
                    ok = await self._generate_nanobanana(
                        headers,
                        prompt,
                        negative_prompt,
                        ref_b64,
                        reference_weight,
                        output_path,
                        strict_reference=strict_reference,
                    )
                    if ok:
                        return True
                    if ref_b64 and self.last_error_code == "invalid_image_request" and idx < len(ref_variants):
                        logger.warning(
                            "NanoBanana 参考图请求过大，降级重试第 %s/%s 档压缩图",
                            idx + 1,
                            len(ref_variants),
                        )
                        continue
                    break
            return False
        elif engine == "nanobanana":
            for idx, ref_b64 in enumerate(ref_variants, start=1):
                ok = await self._generate_nanobanana(
                    headers,
                    prompt,
                    negative_prompt,
                    ref_b64,
                    reference_weight,
                    output_path,
                    strict_reference=strict_reference,
                )
                if ok:
                    return True
                if ref_b64 and self.last_error_code == "invalid_image_request" and idx < len(ref_variants):
                    logger.warning(
                        "NanoBanana 参考图请求过大，降级重试第 %s/%s 档压缩图",
                        idx + 1,
                        len(ref_variants),
                    )
                    continue
                break
            return False
        else:
            raise ValueError(f"不支持的引擎: {engine}")

    async def _generate_seedream(
        self, headers: dict, prompt: str, negative_prompt: str,
        ref_b64: str, ref_weight: int, output_path: str,
    ) -> bool:
        """Seedream 生图（文本生成 / 参考图编辑双模式）"""
        models = [self.seedream_model, "seedream-4-5-251128", "seedream-4.5"]
        tried = set()

        for model in models:
            if not model or model in tried:
                continue
            tried.add(model)

            payload = {
                "model": model,
                "prompt": prompt,
                "size": self.seedream_size,
            }
            if negative_prompt:
                payload["negative_prompt"] = negative_prompt

            # 关键修复：API易图生图需走 input.image 协议，不能用顶层 image/reference_strength。
            if ref_b64:
                payload["input"] = {
                    "image": ref_b64,
                    "function": "edit",
                }
                payload["parameters"] = {"n": 1}
            else:
                payload["n"] = 1

            self._apply_watermark_options(payload)

            if await self._call_api(headers, payload, output_path):
                return True

        return False

    async def _generate_nanobanana(
        self, headers: dict, prompt: str, negative_prompt: str,
        ref_b64: str, ref_weight: int, output_path: str,
        strict_reference: bool = True,
    ) -> bool:
        """Nano Banana Pro 生图（优先参考图编辑，失败再按 strict 决定是否回退文本）"""
        payload_base = {
            "model": self.nanobanana_model,
            "prompt": prompt,
            "size": self.nanobanana_size,
        }
        if negative_prompt:
            payload_base["negative_prompt"] = negative_prompt

        # 1) 先尝试“参考图+文本”模式，提升人物一致性和编辑可控性
        if ref_b64:
            payload_with_ref = dict(payload_base)
            payload_with_ref["input"] = {
                "image": ref_b64,
                "function": "edit",
            }
            payload_with_ref["parameters"] = {"n": 1}
            self._apply_watermark_options(payload_with_ref)
            if await self._call_api(headers, payload_with_ref, output_path):
                return True
            if strict_reference:
                logger.error(
                    "NanoBanana 参考图模式失败，严格参考模式禁止回退纯文本生图 | code=%s",
                    self.last_error_code or "unknown",
                )
                if not self.last_error_code:
                    self._record_error(0, "reference_mode_failed")
                return False
            logger.warning(
                "NanoBanana 参考图模式失败，回退为纯文本生图 | code=%s",
                self.last_error_code or "unknown",
            )

        # 2) 回退为文本生图
        payload = dict(payload_base)
        payload["n"] = 1
        self._apply_watermark_options(payload)
        return await self._call_api(headers, payload, output_path)

    async def _call_api(self, headers: dict, payload: dict, output_path: str) -> bool:
        """调用 API易 生图接口并保存结果"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.api_url}/v1/images/generations",
                    json=payload,
                    headers=headers,
                )

                # 某些模型端点不识别 watermark/logo_info，自动降级重试一次
                if (
                    resp.status_code in (400, 422)
                    and ("watermark" in payload or "logo_info" in payload)
                ):
                    fallback_payload = {
                        k: v for k, v in payload.items() if k not in ("watermark", "logo_info")
                    }
                    logger.warning(
                        "模型可能不支持去水印参数，尝试兼容重试 | model=%s | status=%s",
                        payload.get("model"),
                        resp.status_code,
                    )
                    resp = await client.post(
                        f"{self.api_url}/v1/images/generations",
                        json=fallback_payload,
                        headers=headers,
                    )

                if resp.status_code != 200:
                    logger.error(
                        "API易生图失败: HTTP %s | model=%s | size=%s | %s",
                        resp.status_code,
                        payload.get("model"),
                        payload.get("size"),
                        resp.text[:300],
                    )
                    self._record_error(resp.status_code, resp.text)
                    return False

                data = resp.json()

                # 解析结果图片
                img_bytes = self._extract_image(data)
                if img_bytes is None:
                    self._record_error(200, "empty_image_data")
                    return False

                # 保存图片
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(img_bytes)

                logger.info(f"生图成功: {output_path}")
                return True

        except httpx.TimeoutException:
            logger.error(f"API易生图超时 ({self.timeout}s)")
            self._record_error(0, "timeout")
            return False
        except httpx.RequestError as e:
            logger.error(f"API易生图网络错误: {e}")
            self._record_error(0, str(e))
            return False
        except Exception as e:
            logger.error(f"API易生图异常: {e}")
            self._record_error(0, str(e))
            return False

    def _extract_image(self, data: dict) -> Optional[bytes]:
        """从 API 响应中提取图片数据"""
        # 尝试多种响应格式
        results = data.get("output", {}).get("results", [])
        if not results:
            results = data.get("data", [])

        if not results:
            logger.error(f"API易返回空结果: {data}")
            return None

        item = results[0]
        if isinstance(item, dict):
            b64 = item.get("b64_image") or item.get("b64_json", "")
            url = item.get("url", "")
        else:
            b64 = ""
            url = str(item)

        if b64:
            return base64.b64decode(b64)
        elif url:
            # 同步下载（在 async 上下文中）
            import httpx as httpx_sync
            try:
                resp = httpx_sync.get(url, timeout=30.0)
                return resp.content
            except Exception as e:
                logger.error(f"下载生成图片失败: {e}")
                return None
        else:
            logger.error("API易返回无图片数据")
            return None


class ConcurrentImageGenerator:
    """
    并发图片生成器
    - 初始10线程，动态调整，最大50线程
    - 单图超时240秒
    - 失败自动重试2次
    """

    def __init__(
        self,
        api_key: str = "",
        seedream_model: str = "",
        nanobanana_model: str = "nano-banana-pro",
        disable_watermark: bool = True,
        strict_no_watermark: bool = True,
        best_effort_watermark_cleanup: bool = True,
        watermark_cleanup_margin: float = 0.18,
        watermark_engine: str = "auto",
        iopaint_url: str = "",
        volc_access_key_id: str = "",
        volc_secret_access_key: str = "",
        volc_region: str = "",
        volc_service: str = "",
        initial_concurrency: int = 10,
        max_concurrency: int = 50,
        max_retries: int = 2,
    ):
        self.client = APIYiImageClient(
            api_key=api_key,
            seedream_model=seedream_model,
            nanobanana_model=nanobanana_model,
            disable_watermark=disable_watermark,
        )
        self.strict_no_watermark = strict_no_watermark
        self.best_effort_watermark_cleanup = bool(best_effort_watermark_cleanup)
        self.watermark_cleanup_margin = max(0.08, min(0.35, float(watermark_cleanup_margin)))
        self.watermark_engine = _normalize_watermark_engine(watermark_engine)
        self.iopaint_url = (iopaint_url or app_settings.IOPAINT_URL).strip()
        self.volc_access_key_id = (volc_access_key_id or "").strip()
        self.volc_secret_access_key = (volc_secret_access_key or "").strip()
        self.volc_region = (volc_region or app_settings.VOLC_REGION).strip()
        self.volc_service = (volc_service or app_settings.VOLC_SERVICE).strip()
        self.initial_concurrency = initial_concurrency
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(initial_concurrency)
        self._current_concurrency = initial_concurrency
        self._success_streak = 0
        self._fail_streak = 0
        self._watermark_remover = None
        self._watermark_ready_checked = False
        self._watermark_available = False
        self._last_watermark_error = ""

    @staticmethod
    def _reason_tip(reason_code: str) -> str:
        tips = {
            "insufficient_user_quota": "上游额度不足，请充值 API易 或更换可用 Key。",
            "no_available_channel": "上游渠道不可用，请在 API易 控制台检查模型渠道状态。",
            "rate_limited": "请求频率受限，请降低并发后重试。",
            "unauthorized": "API Key 无效或权限不足，请检查密钥配置。",
            "upstream_server_error": "上游服务异常，可稍后重试。",
            "request_error": "网络请求异常，请检查网络连通性。",
            "missing_reference_image": "严格参考模式缺少有效参考图，请检查底图路径。",
            "reference_mode_failed": "参考图编辑模式失败（严格参考下禁止回退纯文本）。",
            "invalid_image_request": "参考图请求无效（可能请求体过大/格式不兼容），系统已自动降级压缩重试。",
            "strict_no_watermark_cleanup_failed": "严格无水印校验失败，生成结果被拦截。",
            "watermark_remover_unavailable": "去水印引擎不可用（Volc/IOPaint），请检查去水印配置。",
            "watermark_cleanup_exception": "去水印处理异常，请查看后端日志。",
            "unknown_failure": "未知失败，请查看后端日志。",
        }
        return tips.get(reason_code, "任务失败，请查看详细日志。")

    @staticmethod
    def _format_failure_detail(reason_messages: dict[str, str]) -> str:
        if not reason_messages:
            return "未知失败（无错误码）"
        parts = []
        for code, raw_msg in reason_messages.items():
            tip = ConcurrentImageGenerator._reason_tip(code)
            if raw_msg:
                parts.append(f"{code}: {tip} 原始信息: {raw_msg}")
            else:
                parts.append(f"{code}: {tip}")
        return " | ".join(parts)

    def _adjust_concurrency(self, success: bool):
        """动态调整并发数"""
        if success:
            self._success_streak += 1
            self._fail_streak = 0
            # 连续成功10次，增加并发
            if self._success_streak >= 10 and self._current_concurrency < self.max_concurrency:
                self._current_concurrency = min(
                    self._current_concurrency + 5, self.max_concurrency
                )
                self._semaphore = asyncio.Semaphore(self._current_concurrency)
                self._success_streak = 0
                logger.info(f"并发数提升至 {self._current_concurrency}")
        else:
            self._fail_streak += 1
            self._success_streak = 0
            # 连续失败3次，降低并发
            if self._fail_streak >= 3 and self._current_concurrency > 5:
                self._current_concurrency = max(self._current_concurrency - 5, 5)
                self._semaphore = asyncio.Semaphore(self._current_concurrency)
                self._fail_streak = 0
                logger.info(f"并发数降低至 {self._current_concurrency}")

    async def generate_single_with_retry_detail(
        self,
        engine: str,
        prompt: str,
        negative_prompt: str,
        reference_image_path: str,
        reference_weight: int,
        output_path: str,
        strict_reference: bool = True,
    ) -> tuple[bool, str, list[str]]:
        """带重试的单图生成（返回详细失败原因）"""
        reason_messages: dict[str, str] = {}

        def add_reason(code: str, msg: str = ""):
            norm_code = (code or "unknown_failure").strip()
            if not norm_code:
                norm_code = "unknown_failure"
            if norm_code not in reason_messages:
                reason_messages[norm_code] = (msg or "").strip()[:220]

        async with self._semaphore:
            for attempt in range(self.max_retries + 1):
                success = await self.client.generate_image(
                    engine=engine,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    reference_image_path=reference_image_path,
                    reference_weight=reference_weight,
                    strict_reference=strict_reference,
                    output_path=output_path,
                )

                if success:
                    if self.strict_no_watermark:
                        clean_ok = await self._force_remove_watermark(output_path)
                        if not clean_ok:
                            logger.error("强制无水印校验失败，转为失败重试: %s", output_path)
                            if self._last_watermark_error:
                                if "引擎不可用" in self._last_watermark_error:
                                    add_reason("watermark_remover_unavailable", self._last_watermark_error)
                                else:
                                    add_reason("watermark_cleanup_exception", self._last_watermark_error)
                            add_reason("strict_no_watermark_cleanup_failed")
                            success = False
                    elif self.best_effort_watermark_cleanup and self.client.disable_watermark:
                        clean_ok = await self._force_remove_watermark(output_path)
                        if not clean_ok:
                            logger.warning(
                                "best-effort 去水印未完成，保留当前结果: %s | err=%s",
                                output_path,
                                self._last_watermark_error or "unknown",
                            )
                else:
                    if self.client.last_error_code:
                        add_reason(self.client.last_error_code, self.client.last_error_message)
                    else:
                        add_reason("unknown_failure")

                if success:
                    self._adjust_concurrency(True)
                    return True, "", []

                if attempt < self.max_retries:
                    wait = (attempt + 1) * 2
                    detail = self._format_failure_detail(reason_messages)
                    logger.warning(
                        "生图失败，%ss 后重试 (%s/%s) | %s",
                        wait,
                        attempt + 1,
                        self.max_retries,
                        detail,
                    )
                    await asyncio.sleep(wait)

            self._adjust_concurrency(False)
            detail = self._format_failure_detail(reason_messages)
            return False, detail, list(reason_messages.keys())

    async def generate_single_with_retry(
        self,
        engine: str,
        prompt: str,
        negative_prompt: str,
        reference_image_path: str,
        reference_weight: int,
        output_path: str,
        strict_reference: bool = True,
    ) -> bool:
        """
        兼容旧调用：仅返回成功/失败。
        新调用请使用 generate_single_with_retry_detail 获取失败原因。
        """
        ok, _, _ = await self.generate_single_with_retry_detail(
            engine=engine,
            prompt=prompt,
            negative_prompt=negative_prompt,
            reference_image_path=reference_image_path,
            reference_weight=reference_weight,
            strict_reference=strict_reference,
            output_path=output_path,
        )
        return ok

    async def _ensure_watermark_remover(self) -> bool:
        if not self.strict_no_watermark:
            return True
        if self._watermark_ready_checked:
            return self._watermark_available

        try:
            from app.services.watermark_remover import WatermarkRemover

            self._watermark_remover = WatermarkRemover(
                iopaint_url=self.iopaint_url,
                detection_mode="fixed_region",
                engine=self.watermark_engine,
                allow_local_fallback=True,
                volc_access_key_id=self.volc_access_key_id,
                volc_secret_access_key=self.volc_secret_access_key,
                volc_region=self.volc_region,
                volc_service=self.volc_service,
            )
            self._watermark_available = await self._watermark_remover.health_check()
        except Exception as e:
            logger.error("初始化去水印引擎失败: %s", e)
            self._watermark_available = False
            self._watermark_remover = None
            self._last_watermark_error = f"初始化去水印引擎失败: {e}"
        finally:
            self._watermark_ready_checked = True

        if not self._watermark_available:
            logger.error("强制无水印模式开启，但去水印引擎不可用")
            self._last_watermark_error = "去水印引擎不可用（strict_no_watermark=1）"
        return self._watermark_available

    async def _force_remove_watermark(self, output_path: str) -> bool:
        """
        强制执行一次角落去水印，兜底清理供应商残留角标。
        P0要求：若无法清理则视为失败，不允许带水印出图。
        """
        if not output_path:
            return False
        src = Path(output_path)
        if not src.exists():
            return False
        self._last_watermark_error = ""
        if not await self._ensure_watermark_remover():
            return False
        if not self._watermark_remover:
            self._last_watermark_error = "去水印实例未初始化"
            return False

        tmp_path = src.with_suffix(".clean.tmp.jpg")
        try:
            ok = await self._watermark_remover.process_image(
                input_path=str(src),
                output_path=str(tmp_path),
                region="bottom_right",
                margin_ratio=self.watermark_cleanup_margin,
            )
            if not ok or not tmp_path.exists():
                self._last_watermark_error = "去水印处理返回失败"
                return False

            tmp_path.replace(src)
            logger.info("强制去水印完成: %s", src.name)
            return True
        except Exception as e:
            logger.error("强制去水印失败: %s", e)
            self._last_watermark_error = str(e)
            return False
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    async def close(self):
        if self._watermark_remover:
            try:
                await self._watermark_remover.close()
            except Exception:
                pass
