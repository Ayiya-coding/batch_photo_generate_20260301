"""
提示词生成服务 - 对接阿里百炼 API (通义千问, OpenAI 兼容格式)

为每张底图 × 19种人群类型 × 5种风格 = 95 组提示词
"""
import httpx
import json
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from app.core.config import settings
from app.core.constants import CROWD_TYPES, STYLES_PER_TYPE

logger = logging.getLogger(__name__)

# 阿里百炼 OpenAI 兼容端点
BAILIAN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 默认5种风格定义（只描述人物穿搭与气质，不改变背景地点）
DEFAULT_STYLES = [
    {"name": "古典东方", "desc": "新中式/汉服人物造型，强调衣料层次、发饰与仪态，不改背景场景"},
    {"name": "古典西方", "desc": "复古欧式人物造型，强调礼服剪裁、珠宝配饰与气质，不改背景场景"},
    {"name": "现代都市", "desc": "都市通勤与街拍穿搭，强调当季流行单品和妆发，不改背景场景"},
    {"name": "自然清新", "desc": "轻户外与森系穿搭，强调舒适面料、轻妆和自然配色，不改背景场景"},
    {"name": "科幻未来", "desc": "未来感潮流穿搭，强调材质光泽、配件与色彩点缀，不改背景场景"},
]

# 人物造型多样化方向（同风格内也要有造型变化）
STYLE_VARIATION_HINTS = {
    "古典东方": "强调汉服层次、发髻与发簪、袖型与腰封变化；保持背景与机位稳定，但人物身份与面部需重建为目标人群",
    "古典西方": "强调宫廷礼服剪裁、发型卷度、珠宝与手套变化；保持背景与机位稳定，但人物身份与面部需重建为目标人群",
    "现代都市": "强调日常穿搭、妆发细节、配饰与姿态微动作变化；保持背景与机位稳定，但人物身份与面部需重建为目标人群",
    "自然清新": "强调轻盈服饰、自然发型、道具与表情变化；保持背景与机位稳定，但人物身份与面部需重建为目标人群",
    "科幻未来": "强调材质与光泽、发色与装置配饰变化；保持背景与机位稳定，但人物身份与面部需重建为目标人群",
}

SEASONAL_TREND_HINTS = {
    "spring": "current-season trend: lightweight layering, soft pastel or earthy accents, breathable textures",
    "summer": "current-season trend: breathable fabrics, clean silhouettes, bright but controlled color accents",
    "autumn": "current-season trend: layered outfits, knit textures, warm neutral palette with structured outerwear",
    "winter": "current-season trend: thermal layering, wool/down outerwear, rich tones with textured accessories",
}


def _current_season() -> str:
    month = datetime.now().month
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return "winter"


def _crowd_fashion_hint(crowd_type_id: str) -> str:
    if crowd_type_id in {"C01", "C05"}:
        return "age styling: child-safe details, playful styling, comfortable movement-friendly outfit"
    if crowd_type_id in {"C02", "C06", "C08", "C09", "C10", "C11"}:
        return "age styling: youth trend fit, social-media-ready styling, clean silhouette and accessories"
    if crowd_type_id in {"C03", "C07", "C12", "C13", "C14", "C15", "C16", "C17", "C18", "C19"}:
        return "age styling: mature elegant tailoring, premium texture, balanced accessories"
    if crowd_type_id == "C04":
        return "age styling: graceful senior elegance, comfortable premium fabrics, refined classic details"
    return "age styling: fit clothing style to the target crowd age and identity"

# 人群类型详细描述（用于提示词生成）
CROWD_DESCRIPTIONS = {
    "C01": "4-12岁女童，天真可爱，童趣活泼",
    "C02": "18-25岁年轻女性，青春靓丽，时尚优雅",
    "C03": "28-50岁成熟女性，知性优雅，气质端庄",
    "C04": "50岁以上老年女性，慈祥温暖，银发优雅",
    "C05": "4-12岁男童，活泼好动，阳光开朗",
    "C06": "18-45岁年轻男性，阳光帅气，干练有型",
    "C07": "45岁以上成熟男性，沉稳大气，儒雅睿智",
    "C08": "年轻情侣，甜蜜浪漫，亲密互动",
    "C09": "女性闺蜜，亲密友好，青春活力",
    "C10": "男性兄弟，阳刚友谊，潇洒自在",
    "C11": "异性朋友，自然友好，轻松愉快",
    "C12": "母亲与少年儿子，温馨亲情，关爱呵护",
    "C13": "母亲与青年儿子，成熟亲情，相互依靠",
    "C14": "母亲与少年女儿，温柔亲情，甜蜜陪伴",
    "C15": "母亲与青年女儿，知心好友般的母女",
    "C16": "父亲与少年儿子，阳刚亲情，言传身教",
    "C17": "父亲与青年儿子，成熟父子，亦师亦友",
    "C18": "父亲与少年女儿，温暖守护，宠爱有加",
    "C19": "父亲与青年女儿，深沉父爱，默默支持",
}


class PromptGenerator:
    """提示词生成器 - 调用阿里百炼 API"""

    def __init__(self, api_key: str = "", system_prompt: str = ""):
        self.api_key = api_key or settings.BAILIAN_API_KEY
        self.system_prompt = system_prompt or settings.PROMPT_SYSTEM_PROMPT or self._default_system_prompt()
        self.model = "qwen-plus"
        self.timeout = 60.0

    @staticmethod
    def _default_system_prompt() -> str:
        return """你是一个专业的AI绘画提示词生成专家。
你需要根据用户提供的人群类型和风格，生成高质量的图像生成提示词。
你必须以“参考底图”的背景为基础：保持背景场景、构图、机位、透视和光线稳定，但不要继承底图人物身份与五官，需要按目标人群重建人物脸和身份。
你生成的是“同一景点打卡图的多人物版本”：地点、光影、景色要稳定，变化集中在人物类型与穿搭。

提示词要求：
1. 使用英文
2. 包含人物描述（年龄、性别、气质、表情）
3. 包含服装描述（风格、颜色、材质、细节）
4. 包含场景描述（背景、环境、氛围）
5. 包含光线和画面质量描述
6. 适合生成9:16比例的高质量人物写真照片
7. 每个提示词控制在80-150个英文单词

输出格式要求：
- 只输出提示词本身，不要加任何解释或标题
- 正向提示词和负向提示词用 "---NEGATIVE---" 分隔
- 负向提示词简洁，列出需要避免的元素"""

    def _build_user_prompt(
        self,
        crowd_type_id: str,
        style: Dict[str, str],
        reference_context: str = "",
        style_variation_hint: str = "",
    ) -> str:
        crowd_name = CROWD_TYPES.get(crowd_type_id, "未知")
        crowd_desc = CROWD_DESCRIPTIONS.get(crowd_type_id, "")
        season = _current_season()
        seasonal_hint = SEASONAL_TREND_HINTS.get(season, "")
        crowd_fashion = _crowd_fashion_hint(crowd_type_id)
        ref_block = f"\n参考底图特征：{reference_context}" if reference_context else ""
        variation_block = (
            f"\n造型变化方向：{style_variation_hint}"
            if style_variation_hint
            else "\n造型变化方向：保持背景场景与机位稳定，人物身份与面部重建为目标人群，优先变化服装/发型/配饰/妆容，避免重复造型"
        )
        trend_block = (
            f"\n当季穿搭趋势：{seasonal_hint}"
            if seasonal_hint
            else ""
        )
        crowd_fashion_block = f"\n年龄段穿搭约束：{crowd_fashion}"
        return f"""请为以下组合生成一个图像生成提示词：

人群类型：{crowd_name}（{crowd_desc}）
风格：{style['name']}（{style['desc']}）
{ref_block}{variation_block}{trend_block}{crowd_fashion_block}

要求：
- 输出英文正向提示词 + 负向提示词
- 正向提示词和负向提示词用 "---NEGATIVE---" 分隔
- 适合生成9:16比例的人物写真
- 明确要求“仅参考底图背景（景点/光影/景色），不要继承底图人物脸和身份，按目标人群重建人物”
- 明确要求“背景建筑与地标关系保持稳定，不要改换成其他景点”"""

    async def generate_single(
        self,
        crowd_type_id: str,
        style: Dict[str, str],
        reference_context: str = "",
        style_variation_hint: str = "",
    ) -> Tuple[str, str]:
        """
        生成单条提示词

        Returns:
            (positive_prompt, negative_prompt)
        """
        if not self.api_key:
            raise ValueError("百炼 API Key 未配置，请在系统设置中填写")

        user_prompt = self._build_user_prompt(
            crowd_type_id,
            style,
            reference_context=reference_context,
            style_variation_hint=style_variation_hint,
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(BAILIAN_ENDPOINT, json=payload, headers=headers)

            if resp.status_code != 200:
                error_msg = resp.text[:300]
                logger.error(f"百炼 API 错误: HTTP {resp.status_code} - {error_msg}")
                raise RuntimeError(f"百炼 API 调用失败: HTTP {resp.status_code}")

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # 解析正向/负向提示词
            if "---NEGATIVE---" in content:
                parts = content.split("---NEGATIVE---", 1)
                positive = parts[0].strip()
                negative = parts[1].strip()
            else:
                positive = content
                negative = "low quality, blurry, distorted, deformed, ugly, bad anatomy, watermark, text"

            return positive, negative

    async def generate_batch(
        self,
        crowd_type_ids: Optional[List[str]] = None,
        styles: Optional[List[Dict[str, str]]] = None,
        reference_context: str = "",
        progress_callback=None,
    ) -> List[Dict]:
        """
        批量生成提示词

        Args:
            crowd_type_ids: 人群类型ID列表，None则全部19种
            styles: 风格列表，None则使用默认5种
            progress_callback: 进度回调 (current, total, crowd_type, style_name, status)

        Returns:
            [{"crowd_type": "C01", "style_name": "古典东方",
              "positive_prompt": "...", "negative_prompt": "..."}, ...]
        """
        if crowd_type_ids is None:
            crowd_type_ids = list(CROWD_TYPES.keys())
        if styles is None:
            styles = DEFAULT_STYLES

        total = len(crowd_type_ids) * len(styles)
        results = []
        current = 0

        for ct_id in crowd_type_ids:
            for style in styles:
                current += 1
                try:
                    positive, negative = await self.generate_single(
                        ct_id,
                        style,
                        reference_context=reference_context,
                        style_variation_hint=STYLE_VARIATION_HINTS.get(style.get("name", ""), ""),
                    )
                    results.append({
                        "crowd_type": ct_id,
                        "style_name": style["name"],
                        "positive_prompt": positive,
                        "negative_prompt": negative,
                    })
                    if progress_callback:
                        progress_callback(current, total, ct_id, style["name"], "success")
                except Exception as e:
                    logger.error(f"生成提示词失败 {ct_id}-{style['name']}: {e}")
                    results.append({
                        "crowd_type": ct_id,
                        "style_name": style["name"],
                        "positive_prompt": "",
                        "negative_prompt": "",
                        "error": str(e),
                    })
                    if progress_callback:
                        progress_callback(current, total, ct_id, style["name"], "failed")

                # 限流：每次请求间隔 0.3s 避免触发 API 频率限制
                await asyncio.sleep(0.3)

        logger.info(f"批量提示词生成完成: {len(results)}/{total}")
        return results
