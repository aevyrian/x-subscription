# 拾存 · X 下载与归档

**把想留住的帖子，放进自己的空间。**

拾存是一个自托管的 X / Twitter 归档工具：从 X 页面或网站添加帖子与博主，由 gallery-dl 保存文字、图片和视频，再在私人网站里浏览、播放、收藏和整理。后端、网站和 ScriptCat 脚本共用同一套数据；一个 Docker 镜像即可运行。

[正式版 v1.0.1](https://github.com/aevyrian/x-subscription/releases/tag/v1.0.1) · [更新说明](CHANGELOG.md) · [NAS 部署模板](compose.nas.yaml)

## 能做什么

| 使用场景 | 功能 |
| --- | --- |
| 随手保存 | 添加单条帖子，或保存博主全部可读取内容；支持图片、视频和纯文字帖 |
| 持续更新 | 每月、每周、每天或仅一次；默认按月更新，已有媒体跳过下载 |
| 私人浏览 | 单列信息流、全库随机浏览、发帖时间排序、搜索及媒体类型筛选 |
| 找回喜欢的内容 | 帖子书签、标签和备注；博主星标精选；计划支持星标优先、仅看星标和排序 |
| 看图与播放 | 完整适配的图片预览、纯媒体查看器、同帖左右切换、视频播放与拖动进度 |
| 在 X 页面操作 | ScriptCat 浮窗下载当前帖、添加博主订阅、打开对应归档；入口贴合 X 原生浮动控件 |
| 分享 | 复制 X 原帖链接、保存媒体文件；可选独立单帖分享页，支持撤销 |
| 稳定保存 | SQLite 持久队列、失败重试、取消、重启恢复；配置、数据库及下载保存在数据卷 |

界面跟随系统浅色 / 深色，适配 4K、1080P 和手机宽度。信息流保持单列，随机浏览的同一轮分页顺序稳定，点击「换一批」重新打乱。

## 工作方式

```text
X 页面 → ScriptCat 浮窗 ─┐
                        ├→ 后端 API → 持久任务队列 → gallery-dl → 文件仓库
归档网站 ────────────────┘                                  ↓
                                                    网站浏览与管理
```

gallery-dl 负责解析与下载；拾存负责队列、计划、归档索引和界面。无需填写 X 开发者 API Key，X 登录状态通过网站设置中的 Cookie 提供。能下载哪些内容取决于账号权限和 X 实际返回范围。

## Docker 启动

需要已有 Docker Engine 与 Compose。下载正式版源码，或克隆指定版本：

```sh
git clone --branch v1.0.1 --depth 1 https://github.com/aevyrian/x-subscription.git
cd x-subscription
docker compose up -d --build
```

打开 **http://127.0.0.1:8787**，首次进入设置网站密码。默认只允许本机访问；局域网 / NAS 使用见下节。

数据位于 `archive-data` 命名卷，容器内目录如下：

```text
/data/
├── config/       # 数据库、Cookie、设置、头像缓存及 gallery-dl 去重库
└── downloads/    # 按博主保存的媒体和每帖 JSON
```

查看服务状态：`docker compose ps`；健康接口：`http://127.0.0.1:8787/health`。停止用 `docker compose down`，数据卷保留。**不要加 `-v`，它会删除数据卷。**

## 飞牛 / NAS

使用 [compose.nas.yaml](compose.nas.yaml)，它把配置和媒体分开挂载到宿主机。以下操作在新项目目录进行，已有安装先备份，保留其原有数据路径和部署配置。

1. 准备 `data/config` 和 `data/downloads` 两个目录，让容器用户有读写权限。模板默认 UID / GID 为 `10001:10001`；可通过 `PUID`、`PGID` 改为 NAS 上实际拥有这些目录的用户。
2. 将模板端口中的 `XARCHIVE_BIND` 设为 NAS 的局域网 IP；`XARCHIVE_PORT` 默认 `8787`，使用前确认没有被占用。可在同目录私有 `.env` 文件中设置这些值。
3. 执行 `docker compose -f compose.nas.yaml up -d --build`，打开 `http://你的NAS地址:8787`。

Linux 终端准备默认目录权限的示例：

```sh
mkdir -p data/config data/downloads
sudo chown -R 10001:10001 data/config data/downloads
```

只对本项目的新目录设置权限；已有媒体仓库按其实际权限配置挂载，避免递归修改旧目录。外网管理访问请另配受保护的 HTTPS 入口；单帖分享使用独立域名，配置见下文。

## 第一次使用

1. **登录网站**：首次设置至少 8 字符的网站密码，后续用它进入私人归档。
2. **连接 X**：在设置的 X 登录状态中填写当前账号 Cookie，支持 Cookie 请求头或 Netscape cookies.txt 内容，至少包含有效的 `auth_token`。需要代理时，在网络设置填写服务端能访问的代理地址。
3. **添加内容**：填写 X 帖子链接、博主主页或 `@用户名`。博主默认保存全部可读取内容并每月更新，也可选每天、每周或一次性；单帖始终只下载一次。
4. **浏览和整理**：下载任务开始后逐步出现在归档里。计划页可调整频率、暂停、立即更新和星标；下载页可查看进度、取消或重试。

Cookie 仅在服务器使用，不放进脚本，也不会由设置接口回显。**数据卷和数据库备份包含登录信息，请作为私有文件保管。** 网站密码与脚本连接密码分别设置。

## 安装 ScriptCat 脚本

先在浏览器中安装并启用 ScriptCat（脚本猫），再打开归档网站的 **设置 → 连接与扩展**：

1. 设置自己的连接密码，至少 8 字符；多台浏览器可使用同一个密码。
2. 点击页面里的脚本安装入口，或访问 `http://你的归档地址:8787/integrations/xarchive.user.js`，在脚本猫中确认安装。
3. 刷新 X 页面，打开拾存浮窗的「连接设置」，填写归档网站根地址和相同的连接密码，点击「验证并保存」。显示「已连接」后即可下载或添加订阅。

当前脚本为 **1.4.1**。首次安装后，在浮窗连接设置中填写自己的归档网站地址和连接密码。脚本不预设任何站点地址，也不含自动更新链接；更新时从归档网站「连接与扩展」重新安装当前版本。

连接会在启动和保存时向服务器实际验证，错误密码不会保存。改连接密码后，各浏览器需要重新填写；旧版独立连接仍兼容，可在旧版连接管理里撤销。脚本连接只允许验证、提交和跟踪下载任务，不能读取设置或私人媒体库。新装脚本没有反应时，先确认脚本猫已允许它在 X 上运行，再刷新页面。

## 可选：单帖外网分享

不配置时，复制原帖链接和保存文件仍可使用，独立本站分享页不可创建。

配置 `XARCHIVE_SHARE_ORIGIN=https://share.example.com`，将该独立域名反向代理到归档服务，并保留原始 Host。公开入口只应允许 `GET` / `HEAD` 请求访问 `/s/…` 和 `/static/share.*`、`/static/viewer.*`；其余路径拒绝访问。应用也会按分享域名限制请求，管理入口继续保留在内网。

分享只公开主动选择的这一帖原文和媒体，不包含私人备注、标签和其他帖子。可在帖子菜单停止分享；接收方已经保存的副本无法收回。仓库不包含域名、隧道凭据或可直接使用的公网连接配置。

## 本地开发

需要 Python 3.12+。Windows PowerShell：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
./start.ps1
```

macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn xarchive.app:create_app --factory --host 127.0.0.1 --port 8787 --workers 1 --no-access-log
```

本地默认数据目录为 `./data`，与其他机器的数据独立。`XARCHIVE_DATA` 可修改根目录。**保持单进程 `--workers 1`**，避免重复启动计划调度器。

## 备份、升级和旧库接入

- **备份**：停止服务后备份整个 `config` 与 `downloads`，使用命名卷时备份整个卷。程序可重新构建，数据库、Cookie、订阅、收藏和媒体需要自己保存。
- **升级**：先备份数据与 Compose，切换目标版本并重建镜像，继续挂载原数据。已有安装的路径、权限和其他服务配置不要直接被示例覆盖。
- **恢复**：停止服务，恢复对应备份再启动。仅回退程序时尽量保留当前数据；跨版本数据库兼容性需要核对，不能用旧库覆盖升级后新下载的记录。
- **旧库接入**：提供 `python -m xarchive.migrate --help`。先备份新旧数据库与订阅，停止新版服务，再执行一次性导入；迁移工具索引旧媒体、复制去重记录和导入订阅，不搬走原文件。旧媒体需持续以只读方式挂载，并设置 `XARCHIVE_LEGACY_MEDIA`；模板已给出注释位置。它只面向旧 x-subscription 数据格式，不自动同步两套系统，也不会停掉旧容器。

## 项目结构与检查

```text
xarchive/
├── app.py / store.py       # API、认证、数据及持久队列
├── worker.py / engine.py   # 计划调度与 gallery-dl
├── library.py / migrate.py # 归档索引与旧库导入
├── avatars.py / sharing.py # 头像缓存和单帖分享
├── static/                # 网站与媒体查看器
└── integration/           # ScriptCat 脚本
tests/                     # 自动检查
```

安装 `requirements-dev.txt` 后运行：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node --check xarchive/static/app.js
node --check xarchive/integration/xarchive.user.js
```

v1.0.0 的 23 项自动检查涵盖队列恢复、计划、Cookie 解析、接口权限、连接密码、去重配置、旧库导入和分享。此前已在真实 X / ScriptCat / NAS 链路验证新图帖、新视频帖落盘及网页播放；已订阅博主更新补入新帖，重复更新没有新增媒体文件。上述历史实测不代表所有账号、帖子和平台状态。

**已知边界**：X 限流、登录状态、账号权限和搜索返回范围会影响完整性；不保证下载不可见或已删除内容。当前不采集评论、转帖、引用帖和文章；大仓库仍按博主扫描索引。手机仅做模拟尺寸检查；长期自动计划、长视频及 Chrome 中用户自设新密码的完整真实下载链路未全面验证。本次正式发布没有重新执行 NAS 部署或真实 X 下载。

本项目用于保存自己有权访问的内容。发布包不包含运行数据库、Cookie、下载媒体、本机工作记录或旧项目 Git 历史。
