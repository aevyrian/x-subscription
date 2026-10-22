# X Reader V2 前端部署方案（V0.6.0）

> 状态：**待授权**。本文件只做部署准备，未执行任何生产变更。
> 适用范围：Reader 前端 `index.html` 单文件替换。

## 1. 本次部署涉及的文件

| 本地路径 | 线上目标路径 | 容器内路径 | 说明 |
|---|---|---|---|
| `GalleryDL-X订阅/Reader/index.html` | `/vol1/1000/Docker/Reader/app/index.html` | `/app/index.html`（只读挂载） | 唯一需要更新的文件 |

本地 MD5：`3C3B48EEB878EFEEE6D1F99DC81D2F93`
线上 MD5（2026-09-19 只读核验值）：`dad4f41fe0b0ffd70e3462352eb9f094`（V0.5.1 旧视觉）
→ 两者不一致，本次部署就是把该差异上线。

**不涉及**：`backend.py`（本地与线上一致，本次未改动）、`compose.yaml`、`config.json`、`cookies`、`subscriptions.json`、`archive.sqlite`、下载目录、gallerydl 与 metube 容器镜像。

## 2. 是否需要重启容器

**不需要重启。**

依据：`backend.py:824` 的 `_serve_file(Path(__file__).parent / "index.html", ...)` 在每次请求时 `read_bytes()` 实时读取文件，没有进程内缓存；且 `/app` 以只读方式挂载，更新宿主机文件后立即对新请求生效。

因此：替换文件 → 浏览器强制刷新（Ctrl+F5）即可看到新版。若验证异常需要重启，属于可选排查手段，不是必需步骤。

## 3. 部署前备份

```bash
# 1) 记录现状指纹（用于回滚校验）
cd /vol1/1000/Docker/Reader/app
md5sum index.html > /tmp/index.html.before.md5

# 2) 备份到同目录带时间戳，便于快速回滚
cp -p index.html index.html.bak_$(date +%Y%m%d-%H%M%S)

# 3) 同时备份到独立目录，避免误删（可选但推荐）
mkdir -p /vol1/1000/Docker/Reader/backup
cp -p index.html /vol1/1000/Docker/Reader/backup/index.html.$(date +%Y%m%d-%H%M%S)
```

备份不覆盖、不删除任何既有 `.bak` 与历史文件。

## 4. 文件更新步骤

```bash
# 本机（PowerShell）：上传
scp "GalleryDL-X订阅/Reader/index.html" sxfd@10.0.0.10:/vol1/1000/Docker/Reader/app/index.html

# 服务器：校验上传结果与本地一致
md5sum /vol1/1000/Docker/Reader/app/index.html
# 期望：3C3B48EEB878EFEEE6D1F99DC81D2F93

# 服务器：确认容器内可见（只读挂载，应立即同步）
docker exec xreader md5sum /app/index.html
```

注意：文件属主应为 `sxfd:docker`，权限参照既有文件（`-rwxrwx---`）。若上传后属主变化，执行：

```bash
chown sxfd:docker /vol1/1000/Docker/Reader/app/index.html
chmod 770 /vol1/1000/Docker/Reader/app/index.html
```

## 5. 部署后验证

浏览器打开 `http://10.0.0.10:9090`，强制刷新后逐项确认：

1. 视觉：浅色主题、紧凑左侧导航、宽屏时间线（无固定 600px 列）、媒体自适应。
2. 导航底部出现「管理设置」（齿轮）与「切换深浅色」按钮，右上角不再有悬浮齿轮。
3. 点击「管理设置」可打开后台抽屉；订阅列表正常显示昵称/头像/频率；「全部更新」按钮存在。
4. 「立即更新」可提交任务并出现状态反馈；日志弹窗可打开。
5. 时间线：图片正常显示，单图高度约 52vh 不再占满整屏；多图网格正常。
6. 视频：可播放；向下滚动离开可视区后自动暂停且声音停止；滑回不自动播放；再次点击从原进度继续；播放第二个视频时第一个暂停。
7. 详情：点击正文打开详情弹层，打开时背景视频停止；关闭后详情视频停止；评论区与「更新评论」按钮正常。
8. 数字音量：输入 0–150 立即生效并记忆刷新。
9. 移动端（窗口缩到 390px 宽）：无横向滚动条，导航为图标栏，卡片与操作栏完整可见。

接口级快速回归（只读）：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://10.0.0.10:9090/                 # 期望 200
curl -s http://10.0.0.10:9090/api/users | head -c 200                          # 期望 JSON 用户列表
curl -s "http://10.0.0.10:9090/api/tweets?limit=1" | head -c 200               # 期望 JSON 推文
```

## 6. 失败回滚

```bash
cd /vol1/1000/Docker/Reader/app
# 方式 A：用同目录时间戳备份回滚
cp -p index.html.bak_YYYYMMDD-HHMMSS index.html
# 方式 B：用上一轮记录的 md5 校验回滚结果
md5sum index.html   # 期望回到 dad4f41fe0b0ffd70e3462352eb9f094
```

回滚后浏览器强制刷新即可恢复旧视觉，无需重启容器。回滚不影响任何归档数据、订阅配置与任务状态。

## 7. 风险与边界

- 本次只替换前端静态文件，不改后端、不改数据结构、不改容器编排，回滚成本一次拷贝。
- 前端引用的后端接口全部沿用既有契约（`/api/users`、`/api/tweets`、`/api/tweet/{id}`、`/api/admin/*`、`/media/*`、`/avatar/*`），无新增接口。
- 线上 `backend.py` 的 `VERSION` 仍为 `0.5.1`（后端未改），管理面板版本标签会显示 V0.5.1；本次前端变更记于 CHANGELOG 的 V0.6.0 条目。若希望版本号一致，需要另一次后端改动（本次不含）。
- 未在真实生产环境验证的项：真实全屏、真实画中画、真人听感。建议部署后由人工在浏览器点一次确认。

## 8. 待授权动作清单

1. 上传 `index.html` 覆盖线上文件（含备份步骤）。
2. 部署后只读验证（curl 与浏览器检查）。
3. 若验证失败，执行回滚。

以上三项均需明确授权后执行；当前未执行任何一项。
