# X归档站

一个以 gallery-dl 为下载引擎的自托管 X/Twitter 归档系统。下载服务与文件仓库作为基础，ScriptCat 和网站共用接口与任务队列。

```text
ScriptCat 小浮窗 ─┐
                 ├─ 统一接口 / 任务队列 ─ gallery-dl ─ 媒体与帖子仓库
网站管理与阅读 ──┘           ↑                         │
                            └──────── 归档索引 ───────┘
```

## 第一轮功能

- 在 X 页面推送博主、下载单帖、添加订阅、打开对应本地归档。
- 网站统一添加下载、查看队列、生成或撤销脚本连接凭据。
- 复用已有时间线、图片视频阅读、收藏同步、博主编号和持久阅读索引。
- 使用现有 gallery-dl 解析和下载，沿用原媒体目录与 SQLite 去重库。
- 一个业务容器，配置、媒体和缓存分卷保存；无需 Redis 或独立数据库服务。

## 开始使用

在 Docker 主机上进入 `GalleryDL-X订阅`，复制 `.env.example` 为 `.env`，设置 `LOGIN_PASSWORD` 后运行：

```sh
docker compose up -d --build
```

默认访问 `http://127.0.0.1:9090`。局域网地址、X 登录状态、ScriptCat 安装、代理与已有数据迁移见[安装与维护](docs/DEPLOYMENT.md)。脚本原文件为 [xarchive.user.js](GalleryDL-X订阅/integration/xarchive.user.js)。

程序和文件仓库一起设计，升级镜像保留 `/config`、`/downloads` 和 `/cache`。请先用独立试验目录验证，现有服务不要同时写入试验去重库。原项目的 MeTube 与其他下载器不在本次改动范围。

## 程序结构

| 位置 | 职责 |
| --- | --- |
| `GalleryDL-X订阅/service.py` | 单容器启动与子进程管理 |
| `GalleryDL-X订阅/scheduler.py` | 串行调度与 gallery-dl 调用 |
| `GalleryDL-X订阅/shared/` | 任务、订阅持久化、统一提交和脚本认证 |
| `GalleryDL-X订阅/Reader/` | 原网站、统一 API 和下载连接页 |
| `GalleryDL-X订阅/integration/` | ScriptCat 脚本 |
| `tests/` | 隔离测试、容器与真实下载引擎检查 |

## 验证

```sh
python tests/run_gate0.py
docker build -t xarchive:check ./GalleryDL-X订阅
python tests/container_smoke.py
docker run --rm -v "$PWD/tests:/checks:ro" --entrypoint python xarchive:check /checks/engine_smoke.py
```

测试使用临时合成数据。引擎检查实际运行 gallery-dl，从本地 HTTP 服务下载并验证 SQLite 去重；不使用真实 Cookie。GitHub Actions 执行相同的回归、构建和容器检查。

真实 X 登录下载、ScriptCat 扩展在真实 X 页面的权限和交互需单独联调；本地模拟和引擎测试通过不代表这些已经完成。详细证据见[验证记录](docs/VALIDATION.md)。

接口见 [API.md](docs/API.md)，设计见[项目重构规划](项目重构规划.md)。旧版调查文档作为历史参考，部署请以上述新文档为准。
