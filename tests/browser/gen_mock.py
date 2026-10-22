"""生成本地前端原型所需的模拟归档数据（不入 Git 生产数据）。
仅用于视觉与播放验收：构造图片/视频媒体、推文 JSON 与评论 JSON，结构遵循 Reader 契约。
数据根目录默认 <repo>/local-dev（可经 MOCK_DATA_ROOT 覆盖），与本地 Reader 的 DATA_DIR 一致。
"""
import json
import os
import random
from pathlib import Path

from PIL import Image

_HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("MOCK_DATA_ROOT") or (_HERE / ".." / ".." / "local-dev")).resolve()
DATA = ROOT / "data" / "twitter"
MEDIA_SRC = ROOT / "media"


def gradient(w, h, c1, c2, name, ext="jpg"):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    p = name.parent / f"{name}.{ext}"
    img.save(p, quality=90)
    return p


def tweet(user, tid, content, date, fav=120, rt=30, rep=5, view=8000,
          hashtags=None, lang="zh", nick=None):
    d = {
        "tweet_id": tid,
        "author": {
            "name": user,
            "nick": nick or user,
            "id": str(abs(hash(user)) % 10 ** 9),
            "profile_image": "",
            "description": "本地模拟博主",
            "followers_count": 5200,
        },
        "content": content,
        "date": date,
        "hashtags": hashtags or [],
        "favorite_count": fav,
        "retweet_count": rt,
        "reply_count": rep,
        "view_count": view,
        "lang": lang,
    }
    u = DATA / user
    u.mkdir(parents=True, exist_ok=True)
    (u / f"{user}_{tid}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def media(user, tid, idx, ext="jpg"):
    return f"{tid}_{idx}.{ext}" if ext != "jpg" else f"{tid}_{idx}"


def main():
    # 后端进程可能持有目录句柄，rmtree 会失败；改为按文件容错清理 + 覆盖重建
    if DATA.exists():
        for p in list(DATA.rglob("*")):
            if p.is_file() and p.suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".json"):
                try:
                    p.unlink()
                except PermissionError:
                    pass
    # ---- 博主一：旅拍阿蓝，宽幅横幅 ----
    u1 = "demo_travel"
    d1 = tweet(u1, "190001", "今天在海边拍到了绝美的落日，云层像烧起来一样。\n#旅行 #日落 #摄影",
               "2026-09-18 18:24:00", fav=23900, rt=1800, rep=124, view=137900,
               hashtags=["旅行", "日落", "摄影"], nick="旅拍阿蓝")
    gradient(1600, 1000, (255, 140, 60), (90, 30, 90), DATA / u1 / media(u1, "190001", 0))
    # 评论：一级2条 + 二级1条
    cdir = DATA / u1 / "comments"
    cdir.mkdir(exist_ok=True)
    comments = [
        {"id": "190011", "parent_id": "190001", "author_id": "11", "author_name": "路人甲",
         "nick": "路人甲", "content": "太震撼了，这是哪里？", "date": "2026-09-18 19:02:00",
         "lang": "zh", "fav": 3, "url": "https://x.com/路人甲/status/190011"},
        {"id": "190012", "parent_id": "190001", "author_id": "12", "author_name": "玩摄影的",
         "nick": "玩摄影的", "content": "这个色调真舒服，请教一下后期思路", "date": "2026-09-18 19:10:00",
         "lang": "zh", "fav": 8, "url": "https://x.com/玩摄影的/status/190012"},
        {"id": "190121", "parent_id": "190012", "author_id": "121", "author_name": "玩摄影的",
         "nick": "玩摄影的", "content": "补一条：主要是压低高光保留云层细节", "date": "2026-09-18 19:12:00",
         "lang": "zh", "fav": 1, "url": "https://x.com/玩摄影的/status/190121"},
    ]
    (cdir / "190001.json").write_text(json.dumps({
        "tweet_id": "190001", "user": u1, "status": "ok", "updated_ts": 1780000000,
        "error": None, "comments": comments}, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 博主一：四图推文 ----
    d2 = tweet(u1, "190002", "一组蓝调时刻的灯塔，从黄昏到入夜。\n#蓝调时刻",
               "2026-09-17 21:05:00", fav=8700, rt=640, rep=38, view=42000,
               hashtags=["蓝调时刻"], nick="旅拍阿蓝")
    for i, (c1, c2) in enumerate([((30, 60, 140), (10, 15, 60)),
                                  ((20, 90, 160), (5, 30, 90)),
                                  ((40, 110, 180), (12, 20, 80)),
                                  ((15, 40, 110), (25, 15, 60))]):
        gradient(1000, 1000, c1, c2, DATA / u1 / media(u1, "190002", i, "jpg"), "jpg")

    # ---- 博主一：三图推文（首图跨两行） ----
    d3 = tweet(u1, "190003", "城市夜景的三张竖构图，霓虹与雨后的路面。",
               "2026-09-16 22:40:00", fav=4310, rt=210, rep=12, view=18000,
               hashtags=["城市夜景"], nick="旅拍阿蓝")
    for i, (c1, c2) in enumerate([((120, 40, 80), (20, 20, 60)),
                                  ((180, 90, 40), (40, 20, 20)),
                                  ((40, 60, 160), (10, 10, 40))]):
        gradient(1000, 1400, c1, c2, DATA / u1 / media(u1, "190003", i), "jpg")

    # ---- 博主一：纯文字（无媒体，验证时间线仍显示） ----
    tweet(u1, "190004", "今天不拍照，只聊天。最近在整理过去一年的素材，发现最珍贵的画面往往是随手按下的那一张。大家也有这样的时刻吗？",
          "2026-09-15 09:12:00", fav=560, rt=22, rep=86, view=9100, nick="旅拍阿蓝")

    # ---- 博主一：视频推文（真实可播放 webm，用于播放状态验收） ----
    d5 = tweet(u1, "190005", "延时摄影：银河升起的过程，四分钟浓缩成三十秒。\n#延时摄影",
               "2026-09-14 23:30:00", fav=15200, rt=3200, rep=210, view=289000,
               hashtags=["延时摄影"], nick="旅拍阿蓝")
    src = (MEDIA_SRC / "sample_audio.webm") if (MEDIA_SRC / "sample_audio.webm").is_file() else (MEDIA_SRC / "sample.webm")
    vp = DATA / u1 / media(u1, "190005", 0, "webm")
    vp.write_bytes(src.read_bytes())

    # ---- 博主一：第二个视频推文（验证「播放第二个暂停第一个」） ----
    d6 = tweet(u1, "190006", "另一段延时素材，用来核对同时只播放一个视频。",
               "2026-09-13 20:10:00", fav=2100, rt=130, rep=14, view=26000,
               hashtags=["延时摄影"], nick="旅拍阿蓝")
    vp2 = DATA / u1 / media(u1, "190006", 0, "webm")
    vp2.write_bytes(src.read_bytes())

    # ---- 博主二：码农小林 ----
    u2 = "demo_dev"
    tweet(u2, "200001", "把老代码里的三处魔法数字抽成了常量，跑了一遍测试，全绿。今天心情+1 🎉",
          "2026-09-18 10:30:00", fav=340, rt=12, rep=9, view=2600, nick="码农小林")
    gradient(1200, 800, (40, 120, 200), (200, 60, 60),
             DATA / u2 / media(u2, "200002", 0))
    tweet(u2, "200002", "给 Reader 新增了数字音量调节，0-150%，超过 100% 走 Web Audio 增益。",
          "2026-09-17 15:20:00", fav=1200, rt=98, rep=27, view=11000, nick="码农小林")

    # ---- 博主三：光影日记（两图方块） ----
    u3 = "demo_photo"
    tweet(u3, "300001", "黑白胶片感尝试，阴影里的猫。", "2026-09-16 14:00:00",
          fav=980, rt=45, rep=6, view=4200, nick="光影日记")
    for i, (c1, c2) in enumerate([((30, 30, 30), (140, 140, 140)),
                                  ((200, 200, 200), (60, 60, 60))]):
        gradient(1000, 1000, c1, c2, DATA / u3 / media(u3, "300001", i), "jpg")

    total_json = sum(1 for _ in DATA.rglob("*.json"))
    total_media = sum(1 for p in DATA.rglob("*") if p.is_file() and p.suffix.lower() in
                      (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"))
    print(f"mock data ready: users={len(list(DATA.iterdir()))} "
          f"comments_files={len(list((DATA / u1 / 'comments').glob('*.json')))} "
          f"json={total_json} media={total_media}")


if __name__ == "__main__":
    main()