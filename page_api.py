"""
丛雨神社签到 — 页面 API
提供 WebUI 后台查看插件数据的接口
"""

from __future__ import annotations

import os, json
from collections import Counter
from astrbot.api import logger

PLUGIN_NAME = "astrbot_plugin_scratchcard"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


class PluginPageApi:
    """丛雨神社签到页面 API。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api

        register(f"{PAGE_API_PREFIX}/stats", self.get_stats, ["GET"], "整体数据统计")
        register(f"{PAGE_API_PREFIX}/today", self.get_today, ["GET"], "今日数据")
        register(f"{PAGE_API_PREFIX}/users", self.get_users, ["GET"], "全部用户")
        register(f"{PAGE_API_PREFIX}/top", self.get_top, ["GET"], "用户排行")
        register(f"{PAGE_API_PREFIX}/wishes", self.get_wishes, ["GET"], "许愿树数据")

        logger.info("丛雨神社签到页面 API 已注册（5个端点）")

    # ========== 辅助 ==========
    def _load_data_sync(self):
        """同步加载数据"""
        DATA_FILE = "/AstrBot/data/scratchcard_data.json"
        if not os.path.exists(DATA_FILE):
            return {}
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    # ========== 端点 ==========
    async def get_stats(self):
        data = self._load_data_sync()
        return {
            "total_users": len(data),
            "total_draws": sum(u.get("total_draws", 0) for u in data.values()),
            "total_wishes": sum(len(u.get("wishes", [])) for u in data.values()),
            "total_collectibles": sum(len(u.get("collection", [])) for u in data.values()),
        }

    async def get_today(self):
        from datetime import datetime, timezone, timedelta
        CST = timezone(timedelta(hours=8))
        data = self._load_data_sync()
        today_str = datetime.now(CST).strftime("%Y-%m-%d")
        active = 0
        today_draws = 0
        today_feeds = 0
        today_wishes = 0
        for u in data.values():
            if u.get("daily_date") == today_str:
                active += 1
                today_draws += u.get("daily_draws", 0)
                today_feeds += u.get("feed_count", 0)
            for w in u.get("wishes", []):
                if isinstance(w, dict) and w.get("date") == today_str:
                    today_wishes += 1
        return {
            "active_users": active,
            "today_draws": today_draws,
            "today_feeds": today_feeds,
            "today_wishes": today_wishes,
        }

    async def get_users(self):
        data = self._load_data_sync()
        users = []
        for uid, u in data.items():
            users.append({
                "user_id": uid,
                "coins": u.get("coins", 0),
                "total_draws": u.get("total_draws", 0),
                "daily_draws": u.get("daily_draws", 0),
                "collection_count": len(u.get("collection", [])),
                "wish_count": len(u.get("wishes", [])),
                "feed_count": u.get("feed_count", 0),
            })
        return {"users": users}

    async def get_top(self):
        data = self._load_data_sync()
        users = []
        for uid, u in data.items():
            users.append({
                "user_id": uid,
                "coins": u.get("coins", 0),
                "total_draws": u.get("total_draws", 0),
                "collection_count": len(u.get("collection", [])),
            })
        users.sort(key=lambda x: x["coins"], reverse=True)
        return {"users": users[:100]}

    async def get_wishes(self):
        data = self._load_data_sync()
        wishes = []
        for uid, u in data.items():
            for w in u.get("wishes", []):
                if isinstance(w, str):
                    wishes.append({"user_id": uid, "content": w, "date": "未知"})
                else:
                    wishes.append({
                        "user_id": uid,
                        "content": w.get("content", ""),
                        "date": w.get("date", "未知"),
                    })
        wishes.sort(key=lambda x: x["date"], reverse=True)
        return {"wishes": wishes}

