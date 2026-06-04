import json
import os
import random
import asyncio
import re
from datetime import datetime, timezone, timedelta
from collections import Counter
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ==================== 路径配置 ====================
DATA_DIR = "/AstrBot/data"
DATA_FILE = os.path.join(DATA_DIR, "scratchcard_data.json")
OLD_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
FEED_KNOWLEDGE_FILE = os.path.join(DATA_DIR, "feed_knowledge.json")
CST = timezone(timedelta(hours=8))

# ==================== 游戏数值常量 ====================
INITIAL_COINS = 200
DRAW_COST = 10
WISH_COST = 500
DAILY_LIMIT = 10
PITY_THRESHOLD = 90
FEED_LIMIT = 3

# 奖池配置
PRIZE_POOL = [
    {"name": "★金光闪闪大奖★", "prob": 0.01, "range": (80, 150)},
    {"name": "二等奖", "prob": 0.05, "range": (30, 50)},
    {"name": "三等奖", "prob": 0.14, "range": (15, 25)},
    {"name": "四等奖", "prob": 0.30, "range": (8, 14)},
    {"name": "参与奖", "prob": 0.50, "range": (1, 7)},
]

# 收藏品（共10个，分三档稀有度）
COLLECTIBLES = [
    {"name": "穗织的神木叶片", "rarity": "⭐"},
    {"name": "五百年前的铜钱", "rarity": "⭐"},
    {"name": "神社的樱花御守", "rarity": "⭐"},
    {"name": "金色小铃铛", "rarity": "⭐"},
    {"name": "丛雨丸的迷你刀鞘", "rarity": "⭐⭐"},
    {"name": "丛雨的睡衣蝴蝶结", "rarity": "⭐⭐"},
    {"name": "穗织温泉的入浴券", "rarity": "⭐⭐"},
    {"name": "丛雨大人的手写签", "rarity": "⭐⭐⭐"},
    {"name": "丛雨丸的刀鍔碎片", "rarity": "⭐⭐⭐"},
    {"name": "幻之丛雨水晶", "rarity": "⭐⭐⭐"},
]

# 稀有度权重
RARITY_WEIGHTS = {
    "⭐⭐⭐": 1,
    "⭐⭐": 3,
    "⭐": 6,
}

# 参与奖额外掉落收藏品的概率
COLLECTIBLE_BONUS_RATE = 0.005

# 投喂金币上下限
FEED_GOLD_MAX = 30
FEED_GOLD_MIN = -30

# R18 / 敏感词过滤列表（投喂物品名命中则拒绝）
FEED_BLOCKED_KEYWORDS = [
    # 只拦截直接露骨的性器官/性行为词汇，边缘内容不拦
    "肉便器", "小穴", "肉棒", "鸡巴", "阴道", "阴茎", "龟头",
    "口交", "肛交", "射精", "中出", "内射", "颜射", "足交", "乳交",
    "强奸", "轮奸", "迷奸", "精液", "性奴", "尿道",
]

# LLM 调用超时（秒）
LLM_TIMEOUT = 15

# 数据并发锁（保护文件读写，防止并发覆盖）
_data_lock = asyncio.Lock()


# ==================== 数据持久化 ====================
def load_data():
    """读取用户数据，支持自动迁移旧数据。
    
    注意：此方法本身不加锁，调用方应在外层使用 _data_lock。
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    # 自动迁移旧数据
    if not os.path.exists(DATA_FILE) and os.path.exists(OLD_DATA_FILE):
        try:
            with open(OLD_DATA_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(old_data, f, ensure_ascii=False, indent=2)
            logger.info(f"旧数据已自动迁移: {OLD_DATA_FILE} → {DATA_FILE}")
        except Exception as e:
            logger.error(f"旧数据迁移失败: {e}")

    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.error(f"数据文件读取失败，将使用空数据。错误: {e}")
        return {}


def save_data(data):
    """保存用户数据。
    
    注意：此方法本身不加锁，调用方应在外层使用 _data_lock。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, PermissionError) as e:
        logger.error(f"保存数据失败: {e}")


def load_feed_knowledge():
    """读取供奉知识库，返回 {物品名: 金币值}"""
    if not os.path.exists(FEED_KNOWLEDGE_FILE):
        return {}
    try:
        with open(FEED_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.error(f"供奉知识库读取失败: {e}")
        return {}


def save_feed_knowledge(knowledge: dict):
    """保存供奉知识库"""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(FEED_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            json.dump(knowledge, f, ensure_ascii=False, indent=2)
    except (OSError, PermissionError) as e:
        logger.error(f"保存供奉知识库失败: {e}")


# ==================== 用户数据工具 ====================
def get_user(data, user_id, initial_coins=INITIAL_COINS):
    """获取用户数据，如果不存在则用默认值初始化。"""
    if user_id not in data:
        data[user_id] = {
            "coins": initial_coins,
            "wishes": [],
            "daily_date": "",
            "daily_draws": 0,
            "total_draws": 0,
            "collection": [],
            "feed_date": "",
            "feed_count": 0,
        }
    return data[user_id]


def reset_daily_if_new_day(user):
    """检查是否跨天，如果是则重置每日抽卡次数与投喂次数。"""
    today = datetime.now(CST).strftime("%Y-%m-%d")
    if user.get("daily_date") != today:
        user["daily_date"] = today
        user["daily_draws"] = 0
    if user.get("feed_date") != today:
        user["feed_date"] = today
        user["feed_count"] = 0


def do_draw():
    """执行一次抽奖，返回 (奖项名, 金币数)"""
    r = random.random()
    cumulative = 0
    for prize in PRIZE_POOL:
        cumulative += prize["prob"]
        if r <= cumulative:
            low, high = prize["range"]
            coins = random.randint(low, high)
            return prize["name"], coins
    # 兜底
    low, high = PRIZE_POOL[-1]["range"]
    return PRIZE_POOL[-1]["name"], random.randint(low, high)


def get_random_collectible(available_pool=None):
    """按稀有度权重随机出一个收藏品。
    
    Args:
        available_pool: 可选的候选列表，默认使用全局 COLLECTIBLES
    """
    pool = available_pool or COLLECTIBLES
    weights = [RARITY_WEIGHTS.get(c["rarity"], 1) for c in pool]
    return random.choices(pool, weights=weights, k=1)[0]


# ==================== 插件主类 ====================
@register("刮刮乐", "congyusama", "丛雨神社签到！10金币一次，每日抽卡上限，保底收藏品", "1.2.0")
class ScratchCardPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        cfg = config or {}
        self.draw_cost = cfg.get("draw_cost", DRAW_COST)
        self.daily_limit = cfg.get("daily_limit", DAILY_LIMIT)
        self.wish_cost = cfg.get("wish_cost", WISH_COST)
        self.pity_threshold = cfg.get("pity_threshold", PITY_THRESHOLD)
        self.initial_coins = cfg.get("initial_coins", INITIAL_COINS)
        self.feed_enabled = cfg.get("feed_enabled", True)
        # 管理员从配置读取，支持字符串或列表
        admin_cfg = cfg.get("admin_users", [])
        if isinstance(admin_cfg, str):
            self.admin_users = [admin_cfg.strip()]
        elif isinstance(admin_cfg, list):
            self.admin_users = [str(u).strip() for u in admin_cfg]
        else:
            self.admin_users = []

        # LLM 裁定配置
        self.feed_llm_enabled = cfg.get("feed_llm_enabled", True)
        self.feed_llm_provider_id = cfg.get("feed_llm_provider_id", "")
        default_prompt = (
            "吾是夜璃，丛雨神社的魔女。有人供奉了「{item}」给吾。\n"
            "汝要根据吾的喜好裁定回礼金币（-30 到 30 之间）：\n\n"
            "【超喜欢 +20~+30】布丁、团子、草莓大福、冰淇淋等甜食\n"
            "【很喜欢 +10~+19】炸天妇罗、佛跳墙、北京烤鸭、冰镇酸梅汤\n"
            "【还不错 +3~+9】汉堡、火鸡面、炒粉干、芒果、西瓜、卡布奇诺\n"
            "【一般般 -2~+2】白开水、纯水、猫粮、肉桂卷\n"
            "【不喜欢 -10~-3】栗子馒头、腐肉、槟榔、苦瓜\n"
            "【超讨厌 -30~-11】虫子、蜘蛛、蟑螂、鬼、油炸纸巾、头孢加酒\n\n"
            "若供奉的是无意义乱码、人名、危险品，给负分或零分。\n"
            "只回复一个整数数字。"
        )
        self.feed_llm_prompt = cfg.get("feed_llm_prompt", default_prompt)

        # 注册官方插件页面 API
        self._register_page_api()

    def _register_page_api(self) -> None:
        """按需注册官方插件页面 API。"""
        if not hasattr(self.context, "register_web_api"):
            return
        try:
            from .page_api import PluginPageApi
            self.page_api = PluginPageApi(self)
            self.page_api.register_routes()
            logger.info("刮刮乐页面 API 已注册")
        except Exception as exc:
            logger.warning(f"刮刮乐页面 API 注册失败: {exc}")

    # ==================== 内部辅助方法 ====================
    def _check_daily_limit(self, user, user_name):
        """检查今日抽卡次数是否已达上限。
        
        返回 None 表示通过，否则返回错误消息。
        内部会自动重置跨天数据。
        """
        reset_daily_if_new_day(user)
        if user["daily_draws"] >= self.daily_limit:
            return (
                f"{user_name}，汝今天的{self.daily_limit}次抽卡额度已经用完啦，明天再来吧～"
            )
        return None

    def _check_coins(self, user, user_name, cost):
        """检查余额是否足够支付指定费用。
        
        返回 None 表示通过，否则返回错误消息。
        """
        if user["coins"] < cost:
            return (
                f"{user_name}，汝的金币不足啦！当前金币：{user['coins']}，"
                f"需要{cost}金币的说。"
            )
        return None

    def _resolve_collectible(self, user):
        """从用户未收集的收藏品中按权重随机选一个。
        
        若已全收集，则从全部收藏品中随机（允许重复）。
        """
        owned = set(user.get("collection", []))
        available = [c for c in COLLECTIBLES if c["name"] not in owned]
        if not available:
            available = COLLECTIBLES  # 全图鉴了，出啥都行
        return get_random_collectible(available)

    def _do_single_draw(self, user):
        """执行一次抽奖，返回 (prize_name, prize_coins, collectible_or_None, is_pity)
        
        is_pity: True=保底触发, False=欧皇额外掉落, None=未获得收藏品
        """
        prize_name, prize_coins = do_draw()
        user["total_draws"] = user.get("total_draws", 0) + 1

        collectible = None
        is_pity = None

        # 硬保底判定
        if user["total_draws"] >= self.pity_threshold:
            user["total_draws"] = 0
            collectible = self._resolve_collectible(user)
            is_pity = True
        # 非保底时，参与奖有概率额外掉落收藏品
        elif prize_name == "参与奖" and random.random() < COLLECTIBLE_BONUS_RATE:
            collectible = self._resolve_collectible(user)
            is_pity = False

        user["coins"] += prize_coins
        return prize_name, prize_coins, collectible, is_pity

    def _build_draw_result_msg(self, user_name, user, prize_name, prize_coins, 
                               collectible, is_pity, draw_count=1):
        """构建单抽/十连的公共结果消息。"""
        msg = f"🎫 {user_name} 刮开了一张刮刮乐！\n获得：{prize_name} → +{prize_coins} 金币"

        if prize_name == "★金光闪闪大奖★":
            msg += "\n狗修金运气也太好了吧，夜璃大人都惊呆了的说！"

        if collectible:
            if is_pity:
                msg += (
                    f"\n💎 保底触发！获得收藏品："
                    f"【{collectible['name']}】{collectible['rarity']}"
                )
            else:
                msg += (
                    f"\n✨ 欧皇降临！非保底居然出了收藏品："
                    f"【{collectible['name']}】{collectible['rarity']}，"
                    f"汝的运气简直闪瞎吾辈的说！"
                )

        msg += (
            f"\n余额：{user['coins']} 金币 | "
            f"今日已抽：{user['daily_draws']}/{self.daily_limit} | "
            f"距保底：{self.pity_threshold - user['total_draws']}抽"
        )
        return msg

    # ==================== 指令：刮刮乐 ====================
    @filter.command("刮刮乐")
    async def scratch_card(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        async with _data_lock:
            data = load_data()
            user = get_user(data, user_id, self.initial_coins)

            limit_err = self._check_daily_limit(user, user_name)
            if limit_err:
                yield event.plain_result(limit_err)
                event.stop_event()
                return

            coin_err = self._check_coins(user, user_name, self.draw_cost)
            if coin_err:
                yield event.plain_result(coin_err)
                event.stop_event()
                return

            user["coins"] -= self.draw_cost
            user["daily_draws"] += 1
            prize_name, prize_coins, collectible, is_pity = self._do_single_draw(user)

            if collectible:
                user.setdefault("collection", []).append(collectible["name"])

            save_data(data)

        msg = self._build_draw_result_msg(
            user_name, user, prize_name, prize_coins, collectible, is_pity
        )
        yield event.plain_result(msg)
        event.stop_event()

    # ==================== 指令：十连 ====================
    @filter.command("十连", alias={"十连刮刮乐"})
    async def scratch_ten(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()
        cost = self.draw_cost * 10

        async with _data_lock:
            data = load_data()
            user = get_user(data, user_id, self.initial_coins)

            limit_err = self._check_daily_limit(user, user_name)
            if limit_err:
                yield event.plain_result(limit_err)
                event.stop_event()
                return

            # 检查十连后是否超过每日上限
            if user["daily_draws"] + 10 > self.daily_limit:
                remaining = self.daily_limit - user["daily_draws"]
                yield event.plain_result(
                    f"{user_name}，十连需要10次额度，"
                    f"汝今天只剩{remaining}次了，单抽将就一下吧～"
                )
                event.stop_event()
                return

            coin_err = self._check_coins(user, user_name, cost)
            if coin_err:
                yield event.plain_result(coin_err)
                event.stop_event()
                return

            user["coins"] -= cost
            user["daily_draws"] += 10

            results = []
            total_gain = 0
            collectibles_got = []

            for _ in range(10):
                prize_name, prize_coins, collectible, is_pity = self._do_single_draw(user)
                results.append((prize_name, prize_coins))
                total_gain += prize_coins
                if collectible:
                    collectibles_got.append((collectible, is_pity))

            if collectibles_got:
                for c, _ in collectibles_got:
                    user.setdefault("collection", []).append(c["name"])

            save_data(data)

        # 构建消息（锁外执行，减少锁持有时间）
        lines = [f"🎫 {user_name} 刮开了十张刮刮乐！"]
        for i, (name, coins) in enumerate(results, 1):
            lines.append(f"  {i}. {name} +{coins}")
        lines.append(f"总计获得：{total_gain} 金币")

        flash_count = sum(1 for name, _ in results if name == "★金光闪闪大奖★")
        if flash_count > 0:
            lines.append(f"夜璃震惊！竟然出了{flash_count}个金光闪闪大奖的说！")

        if collectibles_got:
            for c, was_pity in collectibles_got:
                if was_pity:
                    lines.append(
                        f"💎 保底触发！获得收藏品："
                        f"【{c['name']}】{c['rarity']}"
                    )
                else:
                    lines.append(
                        f"✨ 欧皇降临！非保底居然出了收藏品："
                        f"【{c['name']}】{c['rarity']}，"
                        f"汝的运气简直闪瞎吾辈的说！"
                    )

        lines.append(
            f"余额：{user['coins']} 金币 | "
            f"今日已抽：{user['daily_draws']}/{self.daily_limit} | "
            f"距保底：{self.pity_threshold - user['total_draws']}抽"
        )

        yield event.plain_result("\n".join(lines))
        event.stop_event()

    # ==================== 指令：许愿 ====================
    @filter.command("许愿")
    async def make_wish(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        message_str = event.message_str.strip()
        wish_content = message_str
        for prefix in ["/许愿", "许愿"]:
            if wish_content.startswith(prefix):
                wish_content = wish_content[len(prefix):].strip()
                break

        if not wish_content:
            yield event.plain_result(
                f"{user_name}，汝还没告诉吾辈要许什么愿呢！"
                f"比如「许愿 想要夜璃的抱抱」这样～"
            )
            event.stop_event()
            return

        async with _data_lock:
            data = load_data()
            user = get_user(data, user_id, self.initial_coins)

            coin_err = self._check_coins(user, user_name, self.wish_cost)
            if coin_err:
                yield event.plain_result(
                    f"{user_name}，许愿需要{self.wish_cost}金币哦！"
                    f"汝当前只有{user['coins']}金币，"
                    f"还差{self.wish_cost - user['coins']}金币的说。"
                )
                event.stop_event()
                return

            user["coins"] -= self.wish_cost
            today = datetime.now(CST).strftime("%Y-%m-%d")
            user["wishes"].append({"content": wish_content, "date": today})
            save_data(data)

        yield event.plain_result(
            f"✨ {user_name} 花费{self.wish_cost}金币向神社许愿：\n"
            f"「{wish_content}」\n"
            f"神社收到啦，愿望已挂上许愿树～剩余金币：{user['coins']}"
        )
        event.stop_event()

    # ==================== 指令：金币 ====================
    @filter.command("金币")
    async def check_coins(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        async with _data_lock:
            data = load_data()
            user = get_user(data, user_id, self.initial_coins)

        yield event.plain_result(f"💰 {user_name} 当前金币：{user['coins']}")
        event.stop_event()

    # ==================== 指令：许愿树 ====================
    @filter.command("许愿树")
    async def wish_tree(self, event: AstrMessageEvent):
        async with _data_lock:
            data = load_data()
            all_wishes = []
            for uid, u in data.items():
                for w in u.get("wishes", []):
                    # 兼容旧格式（纯字符串）和新格式（{content, date}）
                    if isinstance(w, str):
                        all_wishes.append({"content": w, "date": "未知"})
                    else:
                        all_wishes.append(w)
            if not all_wishes:
                yield event.plain_result("🌳 许愿树上还没有挂任何愿望呢～")
                event.stop_event()
                return
            # 按日期排序
            all_wishes.sort(key=lambda x: x["date"], reverse=True)
            lines = ["🌳 丛雨神社 · 许愿树"]
            for i, w in enumerate(all_wishes[:50], 1):
                lines.append(f"  {i}. {w['content']}（{w['date']}）")
            if len(all_wishes) > 50:
                lines.append(f"  ...还有 {len(all_wishes)-50} 条愿望挂在树上")

        yield event.plain_result("\n".join(lines))
        event.stop_event()

    # ==================== 指令：刮刮乐帮助 ====================
    @filter.command("刮刮乐帮助")
    async def scratch_help(self, event: AstrMessageEvent):
        help_text = (
            "🎴 丛雨神社签到 — 帮助\n"
            "\n"
            "📋 指令：\n"
            "/刮刮乐 — 单抽一次（10金币）\n"
            "/十连 — 十连抽（100金币）\n"
            "/金币 — 查看余额\n"
            "/收藏品 — 查看收藏品柜\n"
            "/投喂 <物品> — 投喂吾辈换金币（日限3次）\n"
            "/许愿 <内容> — 花500金币挂愿望上许愿树\n"
            "/许愿树 — 查看所有人的匿名愿望\n"
            "\n"
            "🎯 奖项概率：\n"
            "金光闪闪 1%(80~150) | 二等奖 5%(30~50)\n"
            "三等奖 14%(15~25) | 四等奖 30%(8~14)\n"
            "参与奖 50%(1~7)\n"
            "\n"
            "💎 收藏品：10件，90抽保底，参与奖0.5%欧皇触发\n"
            "⏰ 每日抽卡10次上限，凌晨0点重置\n"
            "💰 新人初始200金币"
        )
        yield event.plain_result(help_text)
        event.stop_event()

    # ==================== 指令：收藏品 ====================
    @filter.command("收藏品")
    async def show_collection(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        async with _data_lock:
            data = load_data()
            user = get_user(data, user_id, self.initial_coins)
            collection = user.get("collection", [])

            if not collection:
                yield event.plain_result(
                    f"{user_name} 还没有任何收藏品哦～"
                    f"多抽刮刮乐，{self.pity_threshold}抽保底必出！"
                )
                event.stop_event()
                return

            counts = Counter(collection)

        lines = [f"💎 {user_name} 的收藏品柜："]
        for item in COLLECTIBLES:
            name = item["name"]
            if name in counts:
                lines.append(f"  {item['rarity']} {name} ×{counts[name]}")

        lines.append(f"共 {len(collection)} 件收藏品")
        yield event.plain_result("\n".join(lines))
        event.stop_event()

    # ==================== 指令：投喂 ====================
    @filter.command("投喂")
    async def feed(self, event: AstrMessageEvent):
        if not self.feed_enabled:
            yield event.plain_result(
                "🍡 投喂功能目前暂停调试中，狗修金正在优化，稍后再来投喂吾辈吧～"
            )
            event.stop_event()
            return

        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        message_str = event.message_str.strip()
        item = message_str
        for prefix in ["/投喂", "投喂"]:
            if item.startswith(prefix):
                item = item[len(prefix):].strip()
                break

        if not item:
            yield event.plain_result(
                f"{user_name}，汝要投喂吾辈什么呀？比如「投喂 团子」这样～"
            )
            event.stop_event()
            return

        # 限制单次投喂物品名称长度，防止滥用
        if len(item) > 50:
            yield event.plain_result(
                f"{user_name}，这名字也太长了吧，吾辈记不住啦！短一点嘛～"
            )
            event.stop_event()
            return

        # R18 / 敏感内容过滤
        item_lower = item.lower()
        for kw in FEED_BLOCKED_KEYWORDS:
            if kw in item_lower:
                yield event.plain_result(
                    f"{user_name}，神社乃清净之地，这等秽物吾辈不收！"
                )
                event.stop_event()
                return

        async with _data_lock:
            data = load_data()
            user = get_user(data, user_id, self.initial_coins)

            # 检查每日投喂次数
            reset_daily_if_new_day(user)
            if user["feed_count"] >= FEED_LIMIT:
                yield event.plain_result(
                    f"{user_name}，今天已经投喂了{FEED_LIMIT}次啦，"
                    f"吾辈吃不下了，明天再来吧～"
                )
                event.stop_event()
                return

            # 神社判定给多少金币
            gold, source = self._judge_feed(item)

            # 如果是新供奉，调用LLM智能裁定
            if source == "fallback_new":
                # 锁内调用 LLM 会阻塞其他用户，先释放锁，裁定后再加锁
                pass  # 占位，实际逻辑在下面

        if source == "fallback_new":
            gold = await self._llm_judge_feed(event, item)
            # 自动记入知识库
            knowledge = load_feed_knowledge()
            knowledge_key = item.lower().strip()
            # 只记录合理范围内的值，防止异常值污染知识库
            if FEED_GOLD_MIN <= gold <= FEED_GOLD_MAX:
                knowledge[knowledge_key] = gold
                save_feed_knowledge(knowledge)

        # 重新加锁写入金币变动
        async with _data_lock:
            data = load_data()
            user = get_user(data, user_id, self.initial_coins)
            # 二次检查每日投喂次数（防竞态）
            reset_daily_if_new_day(user)
            if user["feed_count"] >= FEED_LIMIT:
                yield event.plain_result(
                    f"{user_name}，今天已经投喂了{FEED_LIMIT}次啦，"
                    f"吾辈吃不下了，明天再来吧～"
                )
                event.stop_event()
                return
            # 防止余额被扣成负数
            if gold < 0 and user["coins"] + gold < 0:
                gold = -user["coins"]  # 最多扣到 0

            user["coins"] += gold
            user["feed_count"] += 1
            save_data(data)

        emoji, reaction, feeling = self._get_feed_reaction(item, gold)
        sign = "+" if gold >= 0 else ""

        msg = (
            f"{emoji} {user_name} 供奉了「{item}」！\n"
            f"夜璃{reaction}：{feeling}\n"
            f"{sign}{gold} 金币！剩余金币：{user['coins']} 金币\n"
            f"今日投喂：{user['feed_count']}/{FEED_LIMIT}"
        )

        if source == "fallback_new":
            msg += (
                f"\n📖 夜璃已经把「{item}」记在供奉名录上了，"
                f"下次再有人供奉就知道给多少啦～"
            )

        yield event.plain_result(msg)
        event.stop_event()

    def _judge_feed(self, item: str) -> tuple:
        """判定投喂物品的金币值。
        
        返回 (gold, source)，source 为 "knowledge" / "keywords" / "fallback_new"
        """
        s = item.lower().strip()

        # 0. 查供奉知识库（之前学过的）
        knowledge = load_feed_knowledge()
        if s in knowledge:
            return (knowledge[s], "knowledge")

        # ----- 喜好词库 -----
        likes = [
            (["团子", "丸子", "dango", "糰子", "お団子"], (22, 30)),
            (["布丁", "布甸", "pudding", "プリン"], (18, 26)),
            (["草莓", "いちご", "strawberry", "草莓大福"], (16, 25)),
            (["甜", "糖", "蛋糕", "甜品", "糖果", "蜜", "巧克力", "马卡龙"], (10, 25)),
            (["冰淇淋", "雪糕", "冰激凌", "刨冰", "かき氷"], (12, 22)),
            (["糯米", "年糕", "麻薯", "もち", "大福"], (12, 22)),
            (["果汁", "奶茶", "牛奶", "可可", "抹茶"], (6, 18)),
            (["肉包", "小笼包", "饺子", "烧卖"], (6, 16)),
            (["烤肉", "烧肉", "牛排", "炸鸡"], (6, 16)),
            (["夜璃", "丛雨丸", "巫女", "神社"], (8, 20)),
            (["夸奖", "好看", "可爱", "厉害", "棒", "聪明", "漂亮", "赞美"], (6, 16)),
        ]

        dislikes = [
            (["鬼", "幽灵", "妖怪", "恐怖", "吓人", "ghost", "spooky"], (-30, -20)),
            (["苦瓜", "苦", "药", "中药", "黄莲", "苦参"], (-22, -12)),
            (["辣", "辣椒", "芥末", "wasabi", "辣椒精"], (-16, -6)),
            (["虫子", "虫", "蜘蛛", "蟑螂", "蜈蚣"], (-22, -12)),
            (["过期", "馊", "坏掉", "烂", "发霉"], (-16, -8)),
            (["大蒜", "蒜", "洋葱", "大葱", "韭菜"], (-12, -4)),
            (["骂", "讨厌", "滚", "走开", "烦"], (-10, -4)),
        ]

        score = 0
        matched_keywords = False

        for keywords, (lo, hi) in likes:
            for kw in keywords:
                if kw in s:
                    score += random.randint(lo, hi)
                    matched_keywords = True
                    break

        for keywords, (lo, hi) in dislikes:
            for kw in keywords:
                if kw in s:
                    score += random.randint(lo, hi)
                    matched_keywords = True
                    break

        if matched_keywords:
            return (max(FEED_GOLD_MIN, min(FEED_GOLD_MAX, score)), "keywords")

        # ----- 危险品/化学物（固定给 -30 直接拒绝）-----
        danger_keywords = [
            "麻醉剂", "致幻剂", "镇静剂", "tnt", "炸药", "黑索金", "航弹", "炮弹",
            "氰化钾", "砒霜", "百草枯", "毒药", "甲基苯丙胺", "硝基苯",
            "聚乙二醇", "乙酸铀酰锌钠", "铀", "头孢加酒",
        ]
        for kw in danger_keywords:
            if kw in s:
                return (-30, "keywords")

        # ----- 奇怪/无意义物品（固定给 -10~-5）-----
        weird_keywords = {
            "蚊子": -10, "蟑螂": -25, "小强": -25, "蜘蛛": -20,
            "石头": -5, "腐肉": -10, "油炸纸巾": -10, "槟榔": -8,
            "白开水": 0, "纯水": 0, "水": 0, "猫粮": 1,
        }
        for kw, val in weird_keywords.items():
            if kw in s:
                return (val, "keywords")

        # ----- 人名/群友/社交（固定给 0）-----
        social_keywords = ["群友", "@", "离殇", "索林", "洛兮", "藤原妹红", "墨鱼"]
        for kw in social_keywords:
            if kw in s:
                return (0, "keywords")

        # ----- 边缘内容（给 -5，不拒绝但不鼓励）-----
        edge_keywords = ["涩图", "r18", "h漫", "本子", "袜子", "丝袜"]
        for kw in edge_keywords:
            if kw in s:
                return (-5, "keywords")

        # 没匹配到任何关键词，返回 None 待夜璃（LLM）智能裁定
        return (None, "fallback_new")

    async def _llm_judge_feed(self, event: AstrMessageEvent, item: str) -> int:
        """调用LLM（直调provider，不走pipeline避开RAG）来智能裁定新供奉的金币值"""
        # 如果关闭了 LLM 裁定，直接返回随机默认值
        if not self.feed_llm_enabled:
            return random.randint(0, 6)

        try:
            # 安全转义，然后用用户配置的 prompt 模板替换 {item}
            safe_item = item.replace("{", "{{").replace("}", "}}")
            prompt = self.feed_llm_prompt.replace("{item}", safe_item)

            # 选择 provider：指定了就按 ID 取，否则用当前会话默认
            provider = None
            if self.feed_llm_provider_id:
                provider = self.context.get_provider(self.feed_llm_provider_id)
            if not provider:
                umo = event.unified_msg_origin
                provider = self.context.get_using_provider(umo)
            if not provider:
                raise Exception("没有找到可用的LLM provider")

            # 添加超时保护
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=prompt),
                timeout=LLM_TIMEOUT
            )
            text = resp.completion_text.strip()
            # 正则兜底：从返回文本中提取第一个整数
            nums = re.findall(r'-?\d+', text)
            if nums:
                gold = int(nums[0])
            else:
                gold = 0
            return max(FEED_GOLD_MIN, min(FEED_GOLD_MAX, gold))
        except asyncio.TimeoutError:
            logger.warning("LLM 裁定供奉超时，使用默认值")
            return random.randint(0, 6)
        except Exception as e:
            logger.warning(f"LLM裁定供奉失败，使用保底值: {e}")
            return random.randint(0, 6)

    def _get_feed_reaction(self, item: str, gold: int):
        """根据投喂物品返回 (emoji, reaction, feeling)"""
        s = item.lower().strip()

        specials = {
            # === 甜食（最爱） ===
            "布丁": ("🍮", "双眼放光捧着脸颊幸福地晃来晃去", "呜呜布丁！吾辈在这个时代活下去的全部意义！"),
            "🍮": ("🍮", "双眼放光捧着脸颊幸福地晃来晃去", "汝怎么知道用emoji投喂这招的！天才！"),
            "草莓布丁": ("🍮", "激动地原地转了三圈", "草莓加布丁双重暴击！吾辈腿都软了……"),
            "团子": ("🍡", "开心地接过来咬了一大口", "糯叽叽的～好久没吃到这么好吃的团子了！"),
            "大福": ("🍡", "捧在手心里端详了一会儿才舍得吃", "草莓大福赛高！多来神社供奉！"),
            "冰淇淋": ("🍦", "飞快地舔起来，生怕化了", "夏天就是应该吃冰淇淋的说！"),
            "蛋糕": ("🍰", "端庄地切了一小块分给空气", "唔…奶油好细腻。丛雨大人也来一口？"),
            "巧克力": ("🍫", "掰了一小块含在嘴里", "微苦但很醇……不过吾辈还是更爱甜的！"),
            "马卡龙": ("🍬", "小心翼翼地咬了一口", "好甜！就是太小了……再来十个才够。"),
            "麻薯": ("🍡", "咬了一口拉出长长的丝", "黏黏的好好玩～味道也棒。"),
            "甜甜圈": ("🍩", "套在手指上转了一圈才吃", "好吃又好玩，这东西设计得太天才了。"),
            "蛋挞": ("🥧", "吹了吹热气小口小口吃", "酥皮加嫩蛋奶……吾辈能连吃一打。"),

            # === 正经食物 ===
            "炸天妇罗": ("🍤", "咔嚓咔嚓嚼得嘎嘣脆", "外酥里嫩！给供奉者加功德！"),
            "佛跳墙": ("🍲", "震惊地揭开坛盖，香气扑鼻", "这等珍馐……汝的压岁钱全花在这了吧？"),
            "北京烤鸭": ("🦆", "学着人类的样子用薄饼卷了鸭肉", "皮脆肉嫩！丛雨大人也来一块！"),
            "满汉全席": ("🍽️", "目瞪口呆地看着满满一桌", "这、这得多少金币……汝是真的下血本了。"),
            "桂花陈酿焖牛腩": ("🥩", "夹了一块闭眼品味", "桂花的香气渗进肉里……太会吃了。"),
            "火鸡面": ("🍜", "猛吸一口然后被辣得直哈气", "好辣！但是好爽！水水水——"),
            "炒粉干": ("🍝", "不紧不慢地夹了一筷子", "朴实但踏实，像某个狗修金一样呢。"),
            "芒果": ("🥭", "剥了皮直接啃，汁水横流", "甜！热带的味道！好像看到海边了。"),
            "西瓜": ("🍉", "抱着半个西瓜用勺子挖着吃", "夏天西瓜空调——现代文明的三大奇迹。"),
            "塔斯汀汉堡": ("🍔", "一口咬下去满足地眯起眼", "唔唔！比神社的供品好吃多了！"),
            "肯德基": ("🍗", "抓起鸡翅啃得满嘴油", "垃圾食品……但垃圾就是好吃嘛！"),
            "九转大肠": ("🥓", "犹豫了一下尝了一口", "处理得很干净嘛……居然没有怪味。"),
            "青团": ("🍃", "咬了一口，艾草的清香充满口腔", "春天的味道！虽然现在已经夏天了。"),
            "芝麻馅汤圆": ("🥟", "小心咬开，黑芝麻流出来", "好烫！但是好好吃……传统点心果然最高。"),

            # === 饮品 ===
            "茶": ("🍵", "端庄地抿了一口", "茶道讲一期一会，汝这杯茶吾辈收下了。"),
            "九曲红梅茶": ("🍵", "细细品味后眼前一亮", "这茶有桂花香！汝品位不错。"),
            "冰镇酸梅汤": ("🥤", "咕咚咕咚灌了大半杯", "哈——夏天没有这个活不了。"),
            "伏特加": ("🍸", "好奇地闻了闻然后被呛到", "咳咳咳！吾辈还没成年呢！虽然活了几百年。"),
            "卡布奇诺": ("☕", "小心地喝了一口，沾了一嘴奶泡", "苦中带甜……大人的饮料好复杂。"),

            # === 萌物/二次元 ===
            "猫": ("🐱", "双眼放光地抱起来一顿猛rua", "好可爱好可爱！吾辈也想养一只！"),
            "猫咪": ("🐱", "小心翼翼接过来捧在手心", "喵～咳！吾辈才没有学猫叫！"),
            "小猫": ("🐱", "轻轻摸了摸小猫的脑袋", "软乎乎的……比布丁还软。"),
            "狗": ("🐶", "开心地蹲下来摸摸狗头", "汪汪！……不对吾辈是魔女又不是狗！"),
            "魔法少女": ("✨", "端详了半天", "有微弱魔力波动，虽然不是真魔法但心意收下了。"),
            "星空": ("🌌", "仰头望了望又低头看看手里的供奉", "以前在穗织的夜空下看过很多次。谢谢汝。"),
            "星空下的爱恋": ("🌌", "脸红了一下小心收好", "这种东西也可以供奉的吗……不过吾辈不讨厌。"),
            "青空乐章": ("🎵", "闭上眼睛仿佛听到了什么", "好安静……像是穗织夏天的午后。"),
            "红莲骑士兽": ("⚔️", "眼睛一亮摆出战斗姿势", "好帅！吾辈也想要这个形态！"),
            "附魔金苹果": ("🍎", "拿起来端详，感受到魔力流转", "附魔过的！虽然不是真神器但还挺像那么回事。"),
            "原石": ("💎", "对着光看了看", "唔…普通的石头嘛。不过心意可贵，收下了。"),
            "悲鸣": ("🎶", "安静地听了一会儿", "……虽然叫悲鸣，但听起来并不悲伤呢。"),

            # === 实用/日常 ===
            "小鱼干": ("🐟", "咔嚓咔嚓嚼起来", "虽然吾辈更喜欢甜的，但这个也不错。"),
            "被窝之神": ("🛏️", "感动得差点哭了", "冬天的救星！！汝怎么知道吾辈最怕冷。"),
            "凉爽的电风扇": ("🌀", "凑到风扇前让头发飘起来", "凉快～夏天保命三件套：西瓜冰棍电风扇。"),
            "复活币": ("🪙", "郑重收下放进袖子里", "虽然不确定能不能用，但心意到了。"),
            "token": ("🔑", "一脸老网民的表情", "用API额度当供品……汝是程序员吗？"),
            "空调遥控器": ("🎛️", "对准空调按了一下，凉风徐来", "实用的供奉！吾辈宣布汝是今日最佳。"),
            "无线接收器": ("📡", "研究了一会儿然后点点头", "虽然不懂但感觉很厉害，勉强收下。"),

            # === 危险品/化学物 ===
            "麻醉剂": ("💊", "严肃地把东西推回去", "神社不回收药物，汝自己留着遵医嘱吧。"),
            "致幻剂": ("💊", "皱着眉把东西丢进封印结界", "这种东西别说供奉了，持有都是违法的好吗。"),
            "镇静剂": ("💊", "叹了口气把东西收进医疗废品袋", "神社不是药房，需要的请去医院。"),
            "tnt": ("💣", "吓得差点把东西扔出窗外", "神社是木质建筑！！汝想把吾辈和丛雨大人一起炸飞吗！"),
            "炸药": ("💣", "立刻画了灭火结界把东西裹起来", "汝是来供奉还是来拆迁的？！离神社远点！"),
            "黑索金": ("💣", "脸色发白用结界封印起来", "军用炸药？！汝到底从哪搞来的这种东西！"),
            "航弹": ("💣", "尖叫着把东西丢进异次元空间", "这种吨位的东西也敢拿来供奉！！！汝疯了！！"),
            "炮弹": ("💣", "把东西丢出去然后用结界护住神社", "神社差点变成弹坑！！汝给吾辈出去跑圈反省！！"),
            "氰化钾": ("☠️", "脸色煞白用结界封印起来", "谋杀神明可是大不敬……汝今晚睡觉别关灯。"),
            "砒霜": ("☠️", "吓得把东西扔进封印结界", "五百年了第一次有人拿毒药供奉……真有创意。"),
            "百草枯": ("☠️", "赶紧用结界隔离然后洗手三遍", "百草枯没有解药汝知道吗！！这种东西拿来供奉？！"),
            "甲基苯丙胺": ("🚔", "果断掏出手机按了三个数字", "喂？警察叔叔吗？神社有人供奉违禁品。"),
            "硝基苯": ("🧪", "嫌弃地用两根手指捏着放到远处", "神社不是化学实验室，拿去给理科教室！"),
            "聚乙二醇": ("🧪", "一脸迷惑地看着瓶子", "这什么东西……吾辈虽然是魔女但主修的不是化学。"),
            "乙酸铀酰锌钠": ("🧪", "把东西推得远远的", "名字长得吾辈念都念不完，还带铀字……汝赶紧拿走。"),
            "铀": ("☢️", "直接画了个辐射标志贴上去", "放射性物质！！吾辈还要不要活了！！"),

            # === 奇怪/无意义物品 ===
            "蚊子": ("🦟", "啪地一掌拍死满脸嫌弃", "这种东西也算供奉？！汝对神社有误解！"),
            "石头": ("🪨", "一脸无语地看着汝", "汝认真的？吾辈用这个砸汝脑袋信不信。"),
            "腐肉": ("🤢", "捏着鼻子用两根手指拎到远处", "呜哇臭死了！！都馊了还拿来！！"),
            "油炸纸巾": ("🤨", "一脸迷惑地盯着看", "汝是不是觉得吾辈味觉失灵？"),
            "栗子馒头": ("😑", "嚼了两口放下", "栗子还行馒头太干……差评。"),
            "肉桂卷": ("😐", "小心翼翼地尝了一口", "肉桂味太重了……不是吾辈的菜。"),
            "头孢加酒": ("💀", "吓得脸色煞白一把扔掉", "这个会死人的！！汝是来投喂还是来谋杀的！！"),
            "白开水": ("🥛", "面无表情地喝了一口", "……确实是白开水。汝的心意也和白开水一样透明呢。"),
            "纯水": ("🥛", "喝了一口然后盯着汝", "比白开水还无聊的供奉诞生了。"),
            "水": ("🥛", "看着杯子里的水陷入沉思", "吾辈活了五百年，第一次收到水当供奉。"),
            "猫粮": ("🐾", "闻了闻然后推开", "吾辈不是猫！虽然有时候会喵但吾辈真的不是猫！"),
            "槟榔": ("😤", "一脸嫌弃地丢进垃圾桶", "又致癌又难吃，汝到底图什么。"),

            # === 人名/群友/社交 ===
            "群友": ("🤔", "歪着头一脸困惑", "汝供奉一个群友给吾辈是几个意思？吾辈又不是人口贩子。"),
            "@": ("🙄", "翻了个白眼", "艾特不能当饭吃好吗。换点实际的来。"),

            # === 边缘内容（宽松处理，吐槽为主） ===
            "涩图": ("🙈", "捂住眼睛从指缝里偷看了一眼", "咳……画得还不错。不过下次供奉点正经的！"),
            "r18": ("😑", "面无表情地删掉", "神社不是那种地方。要发去别处发。"),
            "h漫": ("😑", "扫了一眼然后丢进回收站", "画风不错但题材不行。下次拿全年龄的来。"),
            "本子": ("😑", "翻开看了两眼合上", "……封面上写了R18的话不要拿过来啊。"),
            "袜子": ("😤", "捏着鼻子扔回去", "不讲卫生！！穿过的袜子拿来供奉像话吗！"),
            "丝袜": ("😤", "嫌弃地扔回去", "新的也不行！神社不收这些奇怪的东西！"),
        }

        for keyword, (emoji, reaction, feeling) in specials.items():
            if keyword in s:
                return emoji, reaction, feeling

        # ----- 通用食物按金币分层 -----
        if gold > 0:
            if gold >= 25:
                return ("🍡", "双眼放光感动得快哭了", "超好吃！夜璃幸福得要在榻榻米上打滚了的说！")
            elif gold >= 15:
                return ("🍡", "开心地晃着脑袋", "唔唔好好吃～夜璃很喜欢这个！")
            elif gold >= 5:
                return ("🍡", "满意地点点头", "还不错啦，吾辈勉为其难收下了～")
            else:
                return ("🍡", "轻轻尝了一口", "嗯一般般吧～")
        elif gold == 0:
            return ("🍡", "面无表情地嚼了嚼", "嗯……啥味道也没有。空气吗这是。")
        else:
            if gold <= -20:
                return ("💀", "脸色发青差点吐出来", "呜哇！汝是想谋害吾辈吗！神社诅咒今晚就去找汝！")
            elif gold <= -10:
                return ("😖", "皱紧了眉头", "呸呸呸好难吃……记住汝了，下次拿更好的来补偿！")
            else:
                return ("😐", "一脸嫌弃地推开", "唔……味道不太对劲……下次不许拿这个来了。")