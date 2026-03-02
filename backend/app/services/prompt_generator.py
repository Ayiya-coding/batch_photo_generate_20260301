"""
提示词生成服务 - 对接阿里百炼 API (通义千问, OpenAI 兼容格式)

目标：
- 以“人群年龄类型”为主轴
- 根据配置生成 N 条热门穿搭提示词
- 背景仅作为景点/光影参考，不作为风格类别
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.constants import CROWD_TYPES

logger = logging.getLogger(__name__)

# 阿里百炼 OpenAI 兼容端点
BAILIAN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

SEASONAL_TREND_HINTS = {
    "spring": "春季：轻薄叠穿、柔和色彩、透气面料",
    "summer": "夏季：清爽剪裁、轻量材质、亮色点缀",
    "autumn": "秋季：层次叠穿、针织纹理、暖色中性色",
    "winter": "冬季：保暖外套、羊毛羽绒、厚实质感",
}

# 每类人群 5 套穿搭模板（风格名是“服装风格”，不是“画面风格”）
STYLE_PRESETS: Dict[str, List[Dict[str, str]]] = {
    "C01": [
        {"name": "童趣公主裙", "desc": "蓬裙/蝴蝶结/浅色系，突出童趣与可爱", "variation": "发饰与裙摆层次可变化，保持儿童友好"},
        {"name": "新中式童装", "desc": "改良汉元素童装，强调盘扣、刺绣、轻盈披肩", "variation": "可切换马面裙/改良上衣组合"},
        {"name": "校园运动童装", "desc": "运动卫衣/短裙或短裤，活力轻运动", "variation": "强调舒适鞋袜与轻运动道具"},
        {"name": "中国红节日礼服", "desc": "中国红主色的节日礼服或套装，喜庆大气", "variation": "加入金色细节与传统纹样"},
        {"name": "出游休闲童装", "desc": "针织开衫/连衣裙/休闲套装，适合景点打卡", "variation": "突出便携小包与舒适鞋履"},
    ],
    "C02": [
        {"name": "新中式少女汉服", "desc": "轻汉服或新中式套装，注重衣料层次与发饰", "variation": "可变化对襟/立领/披帛等细节"},
        {"name": "学院甜美洋装", "desc": "学院风连衣裙或套装，清新甜美", "variation": "领结、褶裙、针织开衫可替换"},
        {"name": "都市轻通勤套装", "desc": "衬衫+半裙/西装外套+连衣裙，干净利落", "variation": "强调通勤包、简约配饰"},
        {"name": "中国红庆典礼服", "desc": "中国红主色礼服/套装，适合夜景地标打卡", "variation": "可加入刺绣、金线、披肩"},
        {"name": "潮流街头运动风", "desc": "机能外套/运动套装/潮流鞋履，年轻活力", "variation": "强调材质对比与层次叠穿"},
    ],
    "C03": [
        {"name": "轻奢新中式套装", "desc": "改良旗袍/新中式两件套，端庄且有质感", "variation": "强调高级面料与精致配饰"},
        {"name": "商务西装套装", "desc": "修身西装/阔腿裤或半裙，成熟职业感", "variation": "可变化驳领、腰线、配色"},
        {"name": "优雅洋装礼裙", "desc": "中长款连衣裙或礼裙，强调女性线条与气质", "variation": "可变化裙型、袖型、腰饰"},
        {"name": "中国红正式套装", "desc": "中国红为主的正式套装或礼服，稳重大气", "variation": "强调金色点缀和仪式感"},
        {"name": "高级感休闲针织", "desc": "针织上衣+半裙/长裤，舒适但精致", "variation": "可加入围巾、耳饰、手袋"},
    ],
    "C04": [
        {"name": "端庄中式套装", "desc": "中式上衣+长裙/长裤，沉稳典雅", "variation": "强调花纹克制、版型得体"},
        {"name": "优雅针织外套", "desc": "针织开衫+内搭裙装，温和亲切", "variation": "可变化披肩、胸针、丝巾"},
        {"name": "中国红喜庆礼装", "desc": "中国红节庆服饰，喜庆但不过度艳丽", "variation": "强调高级红色层次与质感"},
        {"name": "舒适出游休闲装", "desc": "舒适套装、轻便鞋履，适合景点步行", "variation": "注重保暖与行动便利"},
        {"name": "正式场合洋装", "desc": "简洁正式洋装或套装，体现庄重气质", "variation": "强调端正姿态与细节饰品"},
    ],
    "C05": [
        {"name": "童趣绅士套装", "desc": "小西装/背带裤等童趣绅士穿搭", "variation": "可变化领结、帽饰、袜鞋搭配"},
        {"name": "新中式童装男款", "desc": "中式立领童装，简洁有朝气", "variation": "可变化对襟、盘扣、袖口细节"},
        {"name": "校园运动童装", "desc": "卫衣/运动套装，活泼好动", "variation": "强调机能面料和运动鞋"},
        {"name": "中国红节日童装", "desc": "中国红节日套装，突出喜庆氛围", "variation": "加入传统图案但保持简洁"},
        {"name": "户外机能童装", "desc": "轻户外夹克+工装裤，耐看实用", "variation": "可加入背包、护具等小道具"},
    ],
    "C06": [
        {"name": "都市休闲少年装", "desc": "卫衣/夹克+休闲裤，阳光自然", "variation": "可变化层次叠穿与配色"},
        {"name": "校园运动机能风", "desc": "运动上衣+短裤/长裤，轻机能", "variation": "强调动感姿态与鞋款"},
        {"name": "青春西装套装", "desc": "年轻化西装穿搭，利落清爽", "variation": "可变化内搭T恤或衬衫"},
        {"name": "新中式少年装", "desc": "立领外套/中式内搭，传统与现代融合", "variation": "强调线条干净、配饰克制"},
        {"name": "中国红庆典套装", "desc": "中国红节庆套装，突出打卡仪式感", "variation": "可加入徽章、胸针等细节"},
    ],
    "C07": [
        {"name": "商务西装套装", "desc": "成熟男士西装，稳重有型", "variation": "可变化领带、口袋巾、鞋款"},
        {"name": "中式立领礼装", "desc": "中式立领外套或长衫，儒雅沉稳", "variation": "强调版型与面料纹理"},
        {"name": "高级休闲夹克", "desc": "夹克/针织衫+休闲裤，成熟日常", "variation": "可变化材质与层次"},
        {"name": "中国红庆典礼装", "desc": "中国红主色礼装，庄重且有节庆感", "variation": "强调红金搭配与细节克制"},
        {"name": "户外旅行机能装", "desc": "轻机能外套+耐用裤装，适合景点行走", "variation": "强调实用配件与鞋履"},
    ],
}

PAIR_STYLE_PRESETS: Dict[str, List[Dict[str, str]]] = {
    "C08": [
        {"name": "情侣新中式套装", "desc": "情侣同色系新中式穿搭，呼应但不过度统一", "variation": "强调配色与纹理呼应"},
        {"name": "情侣都市通勤装", "desc": "情侣轻商务/通勤穿搭", "variation": "强调版型协调与鞋包搭配"},
        {"name": "情侣中国红礼服", "desc": "节庆中国红情侣礼服", "variation": "突出仪式感和地标夜景匹配"},
        {"name": "情侣休闲运动装", "desc": "舒适运动休闲情侣装", "variation": "强调活力互动姿态"},
        {"name": "情侣正式西装洋装", "desc": "男西装+女洋装的正式搭配", "variation": "强调质感与礼仪感"},
    ],
    "C09": [
        {"name": "闺蜜新中式双人装", "desc": "闺蜜新中式同主题穿搭", "variation": "配色呼应、款式有区分"},
        {"name": "闺蜜都市轻通勤装", "desc": "闺蜜城市打卡通勤穿搭", "variation": "强调包袋与鞋款差异"},
        {"name": "闺蜜中国红礼装", "desc": "中国红喜庆闺蜜礼装", "variation": "可变化裙型和配饰"},
        {"name": "闺蜜甜美洋装", "desc": "甜美裙装组合，适合轻松打卡", "variation": "强调发饰和妆容清新"},
        {"name": "闺蜜休闲运动装", "desc": "休闲运动双人装，轻松活力", "variation": "注重动态姿态"},
    ],
    "C10": [
        {"name": "兄弟都市休闲装", "desc": "兄弟同色系休闲穿搭", "variation": "版型接近但细节区别"},
        {"name": "兄弟运动机能装", "desc": "运动机能风双人装", "variation": "强调层次与功能配件"},
        {"name": "兄弟商务西装", "desc": "双人西装风格，成熟稳重", "variation": "领带和配色可区分"},
        {"name": "兄弟新中式装", "desc": "中式立领双人穿搭", "variation": "传统元素适度点缀"},
        {"name": "兄弟中国红礼装", "desc": "中国红节庆兄弟装", "variation": "强调庆典感与庄重感"},
    ],
    "C11": [
        {"name": "异性伙伴通勤装", "desc": "异性伙伴轻商务通勤搭配", "variation": "服装风格协调但不情侣化"},
        {"name": "异性伙伴休闲装", "desc": "日常休闲搭配，友好自然", "variation": "强调舒适和自然互动"},
        {"name": "异性伙伴新中式装", "desc": "新中式双人搭配", "variation": "纹理与配色相互呼应"},
        {"name": "异性伙伴正式礼装", "desc": "西装+洋装正式双人装", "variation": "突出地标打卡仪式感"},
        {"name": "异性伙伴中国红套装", "desc": "中国红节庆双人套装", "variation": "强调庄重与喜庆平衡"},
    ],
    "C12": [
        {"name": "母子新中式亲子装", "desc": "母亲与少年儿子新中式亲子搭配", "variation": "亲子同主题不同版型"},
        {"name": "母子都市亲子装", "desc": "通勤休闲亲子穿搭", "variation": "强调亲和与生活感"},
        {"name": "母子中国红礼装", "desc": "中国红节庆亲子礼装", "variation": "注重仪式感和体面"},
        {"name": "母子休闲运动装", "desc": "轻运动亲子搭配", "variation": "强调互动姿态"},
        {"name": "母子正式场合装", "desc": "正式场景亲子搭配", "variation": "突出端庄与少年感平衡"},
    ],
    "C13": [
        {"name": "母子新中式亲子装", "desc": "母亲与青年儿子新中式亲子搭配", "variation": "同主题不同成熟度"},
        {"name": "母子都市通勤装", "desc": "都市通勤亲子搭配", "variation": "强调干练与亲和"},
        {"name": "母子中国红礼装", "desc": "中国红节庆亲子礼装", "variation": "端庄稳重"},
        {"name": "母子休闲出游装", "desc": "出游休闲亲子装", "variation": "舒适面料与自然互动"},
        {"name": "母子正式礼装", "desc": "正式场合亲子套装", "variation": "强调仪式感"},
    ],
    "C14": [
        {"name": "母女新中式亲子装", "desc": "母亲与少年女儿新中式搭配", "variation": "同色系层次变化"},
        {"name": "母女甜美洋装", "desc": "母女甜美裙装搭配", "variation": "强调发饰与裙型呼应"},
        {"name": "母女中国红礼装", "desc": "中国红节庆母女装", "variation": "喜庆但有高级感"},
        {"name": "母女休闲出游装", "desc": "母女轻出游休闲装", "variation": "舒适与拍照友好"},
        {"name": "母女正式礼装", "desc": "正式场景母女搭配", "variation": "强调优雅与亲密感"},
    ],
    "C15": [
        {"name": "母女新中式亲子装", "desc": "母亲与青年女儿新中式搭配", "variation": "注重成熟感与年轻感平衡"},
        {"name": "母女都市通勤装", "desc": "母女都市通勤搭配", "variation": "强调简洁线条"},
        {"name": "母女中国红礼装", "desc": "中国红节庆母女礼装", "variation": "大气庄重"},
        {"name": "母女休闲出游装", "desc": "母女出游休闲装", "variation": "舒适材质与配饰呼应"},
        {"name": "母女正式礼装", "desc": "正式场景母女礼装", "variation": "强调气质与体面"},
    ],
    "C16": [
        {"name": "父子新中式亲子装", "desc": "父亲与少年儿子新中式搭配", "variation": "同主题不同版型"},
        {"name": "父子都市休闲装", "desc": "父子都市休闲搭配", "variation": "强调阳光与干练"},
        {"name": "父子中国红礼装", "desc": "中国红节庆父子礼装", "variation": "稳重喜庆"},
        {"name": "父子运动机能装", "desc": "父子运动机能搭配", "variation": "强调活力互动"},
        {"name": "父子正式西装", "desc": "父子正式西装搭配", "variation": "强调仪式感"},
    ],
    "C17": [
        {"name": "父子新中式亲子装", "desc": "父亲与青年儿子新中式搭配", "variation": "成熟稳重与青年感平衡"},
        {"name": "父子商务通勤装", "desc": "父子商务通勤搭配", "variation": "强调线条利落"},
        {"name": "父子中国红礼装", "desc": "中国红节庆父子礼装", "variation": "庄重体面"},
        {"name": "父子休闲出游装", "desc": "父子出游休闲装", "variation": "舒适实用"},
        {"name": "父子正式西装", "desc": "父子正式西装搭配", "variation": "强调礼仪感"},
    ],
    "C18": [
        {"name": "父女新中式亲子装", "desc": "父亲与少年女儿新中式搭配", "variation": "亲子呼应与童趣平衡"},
        {"name": "父女都市休闲装", "desc": "父女日常休闲搭配", "variation": "自然轻松互动"},
        {"name": "父女中国红礼装", "desc": "中国红节庆父女礼装", "variation": "喜庆但不过度华丽"},
        {"name": "父女甜美洋装", "desc": "父女正式+甜美组合搭配", "variation": "强调父女气质差异化"},
        {"name": "父女正式礼装", "desc": "正式场景父女礼装", "variation": "强调守护感与仪式感"},
    ],
    "C19": [
        {"name": "父女新中式亲子装", "desc": "父亲与青年女儿新中式搭配", "variation": "成熟感与青春感平衡"},
        {"name": "父女都市通勤装", "desc": "父女都市通勤搭配", "variation": "强调高级简洁"},
        {"name": "父女中国红礼装", "desc": "中国红节庆父女礼装", "variation": "突出庄重大气"},
        {"name": "父女优雅洋装", "desc": "父女正式+优雅组合搭配", "variation": "强调服装质感与配饰"},
        {"name": "父女正式礼装", "desc": "正式场景父女礼装", "variation": "突出体面与地标打卡感"},
    ],
}


DEFAULT_STYLES = STYLE_PRESETS["C02"]


def get_styles_for_crowd(crowd_type_id: str) -> List[Dict[str, str]]:
    if crowd_type_id in STYLE_PRESETS:
        return STYLE_PRESETS[crowd_type_id]
    return PAIR_STYLE_PRESETS.get(crowd_type_id, DEFAULT_STYLES)


def build_hot_outfit_styles(crowd_type_id: str, prompt_count: int) -> List[Dict[str, str]]:
    """
    基于单个人群类型动态生成 N 条“热门穿搭”占位风格。
    注意：这里不预设具体穿搭池，真正穿搭细节由大模型结合底图背景/光影生成。
    """
    count = max(1, min(int(prompt_count or 5), 20))
    crowd_name = CROWD_TYPES.get(crowd_type_id, crowd_type_id)
    styles: List[Dict[str, str]] = []
    for idx in range(1, count + 1):
        styles.append({
            "name": f"热门穿搭{idx:02d}",
            "desc": f"基于底图背景与光影，为{crowd_name}推荐当下热门穿搭造型",
            "variation": (
                f"第{idx}/{count}条：与其他条明显区分，必须同时变化服饰、发型、pose、"
                "景别与人物站位，但背景地标和光影保持一致"
            ),
        })
    return styles


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
        return "儿童向：童趣、安全、舒适，方便活动，避免成人化设计"
    if crowd_type_id in {"C02", "C06", "C08", "C09", "C10", "C11"}:
        return "青年向：符合当季流行趋势，线条利落，配饰简洁，适合打卡出片"
    if crowd_type_id in {"C03", "C07", "C12", "C13", "C14", "C15", "C16", "C17", "C18", "C19"}:
        return "成熟向：强调剪裁与面料质感，配色克制，配饰平衡"
    if crowd_type_id == "C04":
        return "长者向：端庄优雅、舒适保暖、细节精致但不过度装饰"
    return "根据人群年龄与身份匹配穿搭风格"


def _recommended_outfit_pack(crowd_type_id: str, season: str) -> str:
    season_token = {
        "spring": "春季",
        "summer": "夏季",
        "autumn": "秋季",
        "winter": "冬季",
    }.get(season, "当季")

    if crowd_type_id in {"C01", "C05"}:
        packs = [
            f"{season_token}童趣休闲套装",
            "新中式童装",
            "童话礼服/小绅士礼装",
            "校园运动童装",
            "节日打卡童装",
        ]
    elif crowd_type_id in {"C02", "C06"}:
        packs = [
            f"{season_token}都市休闲穿搭",
            "轻商务西装/洋装",
            "新中式潮流穿搭",
            "复古正式穿搭",
            "户外旅行打卡穿搭",
        ]
    elif crowd_type_id in {"C03", "C07"}:
        packs = [
            f"{season_token}高级感简约穿搭",
            "商务正式西装套装",
            "新中式优雅穿搭",
            "复古经典礼装",
            "旅行轻奢休闲装",
        ]
    elif crowd_type_id == "C04":
        packs = [
            f"{season_token}端庄舒适穿搭",
            "经典中式礼装",
            "优雅针织外套穿搭",
            "正式场合礼装",
            "家庭出游温暖穿搭",
        ]
    else:
        packs = [
            f"{season_token}趋势休闲穿搭",
            "商务正式穿搭",
            "新中式穿搭",
            "复古穿搭",
            "旅行打卡穿搭",
        ]
    return ", ".join(packs)

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
        return """你是一位人物写真提示词设计师，擅长“同一景点背景下的人物穿搭替换”。
核心原则：
1) 仅参考底图背景和光影：地标、构图、机位、透视、明暗关系必须稳定。
2) 仅改变人物主体：人物身份、脸、服饰、发型、姿态可变。
3) 输出中文，不输出英文。

每条正向提示词必须覆盖5项：
- 人物服饰
- 发型
- 动作pose
- 景别（全身/半身/近景等）
- 人物在背景中的位置（前景/中景/偏左/偏右/靠栏杆等）

输出规则：
- 只输出“正向提示词 + 负向提示词”
- 正向与负向之间用 "---NEGATIVE---" 分隔
- 禁止长篇描写背景色彩/建筑细节，只写“背景严格沿用参考图”
- 负向提示词简洁且强约束“禁止改背景地标与光影”"""

    async def refine_reference_context(self, raw_context: str) -> str:
        """
        将底图特征摘要再用大模型归纳成可读的“背景与光影理解”文本。
        """
        context = (raw_context or "").strip()
        if not context:
            return ""
        if not self.api_key:
            return context

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是摄影场景分析助手。请把输入特征整理为简洁中文，不新增不存在的内容。",
                },
                {
                    "role": "user",
                    "content": (
                        "请基于以下底图特征生成“背景与光影理解”，用于后续人物换装生图。\n"
                        "要求：1) 只写背景地标/光线/色调/构图；2) 40~80字；3) 中文短句。\n"
                        f"特征：{context}"
                    ),
                },
            ],
            "temperature": 0.2,
            "max_tokens": 220,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(BAILIAN_ENDPOINT, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.warning("底图AI理解失败，回退原始特征: HTTP %s", resp.status_code)
                return context
            data = resp.json()
            refined = data["choices"][0]["message"]["content"].strip()
            return refined or context
        except Exception as e:
            logger.warning("底图AI理解异常，回退原始特征: %s", e)
            return context

    def _build_user_prompt(
        self,
        crowd_type_id: str,
        style: Dict[str, str],
        reference_context: str = "",
        style_variation_hint: str = "",
        style_index: int = 1,
        style_total: int = 5,
    ) -> str:
        crowd_name = CROWD_TYPES.get(crowd_type_id, "未知")
        crowd_desc = CROWD_DESCRIPTIONS.get(crowd_type_id, "")
        crowd_fashion = _crowd_fashion_hint(crowd_type_id)
        ref_block = "\n参考图约束：背景图、背景色彩、光影、地标、构图、机位全部严格沿用上传图，不重新设计背景。"
        variation_block = (
            f"\n造型变化方向：{style_variation_hint}"
            if style_variation_hint
            else "\n造型变化方向：仅变化人物服装/发型/配饰/姿态，人物身份重建，避免重复造型"
        )
        style_order_block = f"\n当前生成序号：第 {style_index}/{style_total} 条（要求与其他条明显不同）"
        crowd_fashion_block = f"\n年龄段穿搭约束：{crowd_fashion}"
        reference_meta_block = (
            f"\n底图分析标签（仅作约束，不要展开背景描写）：{reference_context}"
            if reference_context
            else ""
        )
        return f"""请为以下组合生成一个图像生成提示词：

人群类型：{crowd_name}（{crowd_desc}）
风格：{style['name']}（{style['desc']}）
{ref_block}{reference_meta_block}{style_order_block}{variation_block}{crowd_fashion_block}

要求：
- 输出中文正向提示词 + 负向提示词
- 正向提示词和负向提示词用 "---NEGATIVE---" 分隔
- 适合生成9:16比例的人物写真
- 这是单人主体替换任务：保持背景不变，仅替换人物造型与穿搭
- 明确要求“严格参考已上传图片背景，不要改背景颜色、光影、地标、建筑、构图”
- 必须写清：服饰、发型、动作pose、景别、人物在背景中的位置
- 强调“主写服装风格与人物造型，不要长篇描写背景细节”
- 强调“优先写清服装/发型/配饰/姿态，不要过度强调面部细节，确保后续换脸可用（无遮挡脸部）”"""

    async def generate_single(
        self,
        crowd_type_id: str,
        style: Dict[str, str],
        reference_context: str = "",
        style_variation_hint: str = "",
        style_index: int = 1,
        style_total: int = 5,
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
            style_index=style_index,
            style_total=style_total,
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

            # 解析正向/负向提示词（兼容中英文分隔）
            separators = [
                "---NEGATIVE---",
                "---负向---",
                "【负向提示词】",
                "负向提示词：",
                "负面提示词：",
                "负向：",
            ]
            positive = content
            negative = ""
            for sep in separators:
                if sep in content:
                    parts = content.split(sep, 1)
                    positive = parts[0].strip()
                    negative = parts[1].strip()
                    break

            if not negative:
                negative = (
                    "低清晰度、模糊、畸形手、肢体异常、五官错位、背景错景、地标错位、"
                    "保留原人脸、遮挡脸部、口罩、墨镜、水印、文字、噪点、过曝"
                )

            return positive, negative

    async def generate_batch(
        self,
        crowd_type_ids: Optional[List[str]] = None,
        styles: Optional[List[Dict[str, str]]] = None,
        reference_context: str = "",
        prompt_count: int = 5,
        progress_callback=None,
    ) -> List[Dict]:
        """
        批量生成提示词

        Args:
            crowd_type_ids: 人群类型ID列表，None则全部19种
            styles: 风格列表（仅在单一人群调试时传入），None则按人群动态生成热门穿搭
            prompt_count: 每个人群生成条数（仅 styles 为 None 时生效）
            progress_callback: 进度回调 (current, total, crowd_type, style_name, status)

        Returns:
            [{"crowd_type": "C01", "style_name": "童趣公主裙",
              "positive_prompt": "...", "negative_prompt": "..."}, ...]
        """
        if crowd_type_ids is None:
            crowd_type_ids = list(CROWD_TYPES.keys())
        styles_by_crowd = {}
        for ct_id in crowd_type_ids:
            styles_by_crowd[ct_id] = styles if styles is not None else build_hot_outfit_styles(ct_id, prompt_count)
        total = sum(len(v) for v in styles_by_crowd.values())
        results = []
        current = 0

        for ct_id in crowd_type_ids:
            ct_styles = styles_by_crowd.get(ct_id, [])
            style_total = len(ct_styles)
            for idx, style in enumerate(ct_styles, start=1):
                current += 1
                try:
                    positive, negative = await self.generate_single(
                        ct_id,
                        style,
                        reference_context=reference_context,
                        style_variation_hint=style.get("variation", ""),
                        style_index=idx,
                        style_total=style_total,
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
