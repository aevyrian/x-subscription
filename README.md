# 拾存 · X 下载与归档

**把想留住的帖子，放进自己的空间。**

拾存是一个自托管的 X / Twitter 归档工具：从 X 页面或网站添加帖子与博主，由 gallery-dl 保存文字、图片和视频，再在私人网站里浏览、播放、收藏和整理。后端、网站和 ScriptCat 脚本共用同一套数据；一个 Docker 镜像即可运行。

[正式版 v1.0.6](https://github.com/aevyrian/x-subscription/releases/tag/v1.0.6) · [更新说明](CHANGELOG.md) · [NAS 部署模板](compose.nas.yaml)

## 能做什么

| 使用场景 | 功能 |
| --- | --- |
| 随手保存 | 添加单条帖子，或保存博主全部可读取内容；支持图片、视频和纯文字帖 |
| 持续更新 | 三天、七天、月、季度、年或仅一次；默认季度，自动更新在白天分散安排，已有媒体跳过下载 |
| 私人浏览 | 单列信息流、全库随机浏览、发帖时间排序、搜索及媒体类型筛选 |
| 全站搜索 | 名字、历史昵称或X号关联帖子、书签、周期、最近完整更新和任务，支持立即更新 |
| 找回喜欢的内容 | 帖子书签、标签和备注；博主星标精选；计划支持星标优先、仅看星标和排序 |
| 看图与播放 | 完整适配的图片预览、纯媒体查看器、同帖左右切换、视频播放与拖动进度 |
| 在 X 页面操作 | ScriptCat 浮窗下载当前帖、添加博主订阅、打开对应归档；入口贴合 X 原生浮动控件 |
| 分享 | 复制 X 原帖链接、保存媒体文件；可选独立单帖分享页，支持撤销 |
| 稳定保存 | SQLite 持久队列、失败重试、取消、重启恢复；配置、数据库及下载保存在数据卷 |

界面跟随系统浅色 / 深色，适配 4K、1080P 和手机宽度。下载页固定使用轻松浏览卡片，保留筛选、详情和原有操作，不再提供布局选择。信息流保持单列，随机浏览的同一轮分页顺序稳定，点击「换一批」重新打乱。

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
git clone --branch v1.0.6 --depth 1 https://github.com/aevyrian/x-subscription.git
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
2. 模板默认只绑定本机环回地址；需要在内网直接访问时，将 `XARCHIVE_BIND` 设为 NAS 的局域网 IP。`XARCHIVE_PORT` 默认 `8787`，使用前确认没有被占用。可在同目录私有 `.env` 文件中设置这些值。
3. 执行 `docker compose -f compose.nas.yaml up -d --build`。内网通过 `http://NAS内网IP:8787` 访问，外网继续通过配置好的可信 HTTPS 域名访问；两个入口都需要原网站密码。

Linux 终端准备默认目录权限的示例：

```sh
mkdir -p data/config data/downloads
sudo chown -R 10001:10001 data/config data/downloads
```

只对本项目的新目录设置权限；已有媒体仓库按其实际权限配置挂载，避免递归修改旧目录。内网可直接使用 NAS 私有 IP 的 HTTP 入口，外网使用带可信证书的 HTTPS 域名。**内网 HTTP 传输不加密，网站密码、会话及填写的 Cookie 等敏感内容可能被同网段监听；只在自己信任的内网使用。** 模板不自动配置证书或 HTTPS。单帖分享使用独立域名，配置见下文。

HTTPS 反向代理或隧道应保留原始 Host，并传递 `X-Forwarded-Proto: https`。Uvicorn 默认启用代理头处理，但只信任本机环回地址；容器内代理需将 `FORWARDED_ALLOW_IPS` 配置为受控代理地址或专用 Docker 网段，并保持该网段在重建后稳定。后端端口按需要绑定 NAS 内网地址、本机环回或专用容器网络，不无条件信任任意来源的代理头。上线后需要实际验证 HTTPS 登录、Secure 会话和同源提交，不能仅凭域名或健康接口判断完成。

### 当前 NAS 的杭州 FRP / HTTPS 入口（2026-10-24）

| 用途 | 入口 | 本轮处理 |
| --- | --- | --- |
| 杭州管理入口 | **https://x.2c2d.cn** | 新增，使用原网站密码和原脚本连接密码 |
| NAS 内网 | http://10.0.0.10:8787 | 保留原绑定和数据 |
| 原 Cloudflare 外网入口 | https://x.522661.xyz | 保留，作为独立备用入口 |
| 原单帖分享 | https://x-share.522661.xyz | 保留原路由及权限边界 |

实际后端在飞牛 `L8 / 10.0.0.10`，容器 `xarchive` 监听 8787，通过宿主机 `10.0.0.10:8787` 和 `xarchive_default` 网络提供服务。容器地址本轮为 `172.25.0.3`，稳定调用名为 `archive:8787`；网络为 `172.25.0.0/16`。应用 Compose 仍为 `/vol1/1000/Docker/XArchive/compose.yaml`，本轮未修改或重建应用和 Tunnel。

```text
HTTPS x.2c2d.cn:443
  → 杭州公网入口 118.178.124.213:443 → frps:8443
  → NAS 独立 frpc → 127.0.0.1:9443 → Caddy:443（TLS 终止）
  → 同一私有网络 archive:8787（HTTP）

HTTP x.2c2d.cn:80
  → 杭州公网入口 :80 → frps:8080
  → NAS 独立 frpc → 127.0.0.1:9080 → Caddy:80
  → ACME 验证，普通请求跳转 HTTPS
```

NAS 复用 `/var/apps/frpc/target/app/frpc`（0.71.0），独立连接杭州已有 frps 0.71.1 的 **7000** 控制端口。新增代理名为 `xarchive-nas-http-hz`、`xarchive-nas-https-hz`，均使用 `customDomains = ["x.2c2d.cn"]`，不设置 `subdomain`。杭州 `subDomainHost = "spee.2c2d.cn"` 保持原值，访问域名不是 `x.spee.2c2d.cn`。HTTPS 代理使用 PROXY v2，Caddy 只接受 Docker 网关 `172.25.0.1/32` 的 PROXY 信息，再传递原始 Host、HTTPS 协议及客户端地址给应用；应用原可信代理范围保持不变。

| 配置或数据 | NAS 实际路径 / 说明 |
| --- | --- |
| 新入口目录 | `/vol1/1000/Docker/XArchive/hangzhou/` |
| 独立客户端 | `hangzhou/frpc.toml`；包含复用认证，权限 0600，不复制到文档或仓库 |
| 客户端服务 | `/etc/systemd/system/xarchive-frpc-hangzhou.service`；已启用开机启动 |
| 客户端管理接口 | 仅 `127.0.0.1:7401`；原生 frpc 的 7400 与空配置不变 |
| HTTPS 站点 | `hangzhou/Caddyfile` |
| 入口 Compose | `hangzhou/compose.yaml`；容器 `xarchive-hangzhou-caddy`，Caddy 2.11.7，固定镜像摘要 |
| 证书及续期资料 | `hangzhou/caddy-data/`；Caddy 状态另存 `hangzhou/caddy-config/`，均持久化；包含私钥，不公开或提交 |
| 改前备份 | `/vol1/1000/Docker/XArchive/backups/20261024-hangzhou-entry/`，保留原应用 Compose 与原生 frpc 配置 |

**DNS 与供应商绑定：** 最终经阿里云两个权威 DNS、Google 和 Cloudflare 公共解析核实，`x.2c2d.cn` 只返回杭州 `118.178.124.213`。本轮未迁移 DNS，也未修改解析记录；开工时部分查询曾同时返回北京 `123.56.116.9`，证书验证也曾命中该旧地址，后续权威解析收敛后申请成功。北京 Caddy 没有此域名站点，另有名为 `x` 的旧 TCP 代理监听 35441，实际客户端目标未证实，不能认定它属于拾存，故保留未动。若未来重新加入北京 A 记录，会分流此域名；任何历史 `x.2c2d.cn:35441` 链接也受该域名解析影响。供应商要求绑定时，完整填写 **`x.2c2d.cn`**，主机记录填写 **`x`**；本轮已实测 80/443 到新入口，无额外后台绑定步骤。

**证书与续期：** Let's Encrypt YE2，SAN 为 `x.2c2d.cn`；本轮证书有效至 **2027-01-22 18:59:08（UTC+8）**，实时 TLS 1.3 与受信任证书校验通过。Caddy 自动申请和续期，无额外定时任务；须保持该域名指向杭州、80/443 转发及新入口服务在线，并保留 `caddy-data`。原 Cloudflare 边缘证书没有复制出来；这是一套独立证书。未来可在同一 Caddy 容器增加站点，并在 frpc 增加各域名的完整匹配，本轮仅配置 X。配置行为依据 [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https) 与 [frp 域名代理](https://gofrp.org/en/docs/examples/vhost-http/)。

**实际验收：** 三入口健康与 worker、首页、前端 JS、ScriptCat 1.4.4 均通过，静态文件摘要相同；匿名管理与提交接口返回 401，同源空登录返回 422、跨源登录返回 403。用户用原密码在杭州入口登录并完成脚本连接；浏览器读取设置、帖子和任务均为 200，登录 Cookie 为 Secure / HttpOnly / SameSite=Strict，页面图片解码正常。用户实际脚本任务约 7 秒成功，后台来源为 `scriptcat:password`，新增 1 帖、3 份媒体；任务详情和归档可读，图片及视频 Range 请求均返回 206。原 LAN 的已有登录 API 仍为 200；原外网本轮核实登录页、鉴权边界、资源和健康，未重复输入个人密码。原应用与 Tunnel 容器 ID、应用 Compose、原生 frpc 配置不变。

证据在本机 `.work/hangzhou-entry-20261024/` 的 `tls-verified.json`、`public-functional-verification.json`、`authenticated-browser.json`、`script-and-media-verified.json`、`original-browser-regression.json`、`verified-runtime.json`。北京管理机独立外网探测新入口得到杭州 IP、200、证书校验 0；该机探测原 Cloudflare 入口时 TLS 超时，本机 curl 与浏览器则正常，此网络差异未扩展排查，也未修改原隧道规则。本轮未做 NAS / 杭州重启演练，也未等到实际续期日；开机启动和自动续期配置已回读，不能将其当作未来已发生的验证。

**撤销本次入口（保留数据）：** 在 NAS 执行以下两条，只停新增入口，不影响原应用、原 Tunnel、原生 frpc、数据库和媒体：

```sh
sudo systemctl disable --now xarchive-frpc-hangzhou.service
sudo docker compose -f /vol1/1000/Docker/XArchive/hangzhou/compose.yaml stop
```

保留 `hangzhou/` 和改前备份，**不删目录、不删除卷、不使用 `down -v`**。恢复时先对同一 Compose 执行 `up -d`，再 `systemctl enable --now xarchive-frpc-hangzhou.service`。本轮没有改动杭州 frps、北京代理和 DNS 配置，撤销无需恢复它们；新域名会停止服务，两个原管理入口继续使用。

## 第一次使用

1. **登录网站**：首次设置至少 8 字符的网站密码，后续用它进入私人归档。
2. **连接 X**：在设置的 X 登录状态中填写当前账号 Cookie，支持 Cookie 请求头或 Netscape cookies.txt 内容，至少包含有效的 `auth_token`。需要代理时，在 **设置 → 下载与更新 → 下载代理** 填写服务端可访问的 HTTP / HTTPS 代理地址；地址和凭据不会回显，留空保留现有配置，勾选「清除当前代理」可恢复直接连接。
3. **添加内容**：填写 X 帖子链接、博主主页或 `@用户名`。博主默认保存全部可读取内容并按季度更新，也可选三天、七天、一个月、一年或一次性；单帖始终只下载一次。

自动更新的开始时间随机安排在**北京时间08:00–18:00**，已开始的长任务允许完成。首次升级将现有全部订阅统一改为季度，日期与时刻分散在未来三个月，原已暂停的计划继续暂停；之后各自按周期推进。自动任务有派发间隔，过期计划会分散恢复，避免启动后整库同时入队；手动“立即更新”和手动重试仍立即入队。一次性不会再自动更新，旧版脚本的每天周期继续兼容。

下载页可直接取消任务、删除已结束任务记录，无二次确认。批量删除、重试、取消覆盖**当前搜索和筛选的全部任务**，包括其他分页；不适用的项目会跳过并显示数量。删除任务不删除帖子、媒体或订阅；活动任务需先取消再删除记录。

所有页面共用上方的搜索框：输入名字、历史昵称或 `@X号` 后，搜索结果同时展示该博主的归档帖子和更新信息。博主卡可直接查看书签、调整计划、筛选任务或立即更新；“最近完整更新”只取完整博主任务成功时间，任务记录删除后仍保留；单帖成功不表示博主已全部更新。“内容收录到”显示已有归档中最新帖子的时间，不保证包含X未开放的内容。

任务卡及详情中的X链接可以打开对应博主或原帖，方便用保存Cookie的同一X账号检查权限或手动关注。明确账号限流后，整个下载队列统一等待，手动更新仍可入队但不能绕过冷却；普通媒体429警告后成功不会再误报账号限流。没有内容或权限受限的任务需要按X页面实际状态判断，关注并不保证可下载。
4. **浏览和整理**：下载任务开始后逐步出现在归档里。计划页可调整频率、暂停、立即更新和星标；下载页可查看进度、取消或重试。

Cookie 仅在服务器使用，不放进脚本，也不会由设置接口回显。**数据卷和数据库备份包含登录信息，请作为私有文件保管。** 网站密码与脚本连接密码分别设置。

## 安装 ScriptCat 脚本

先在浏览器中安装并启用 ScriptCat（脚本猫），再打开归档网站的 **设置 → 连接与扩展**：

1. 设置自己的连接密码，至少 8 字符；多台浏览器可使用同一个密码。
2. 点击页面里的脚本安装入口，在脚本猫中确认安装；也可在归档网站根地址后加 `/integrations/xarchive.user.js` 访问脚本。
3. 刷新 X 页面，打开拾存浮窗的「连接设置」：内网填写 `http://NAS内网IP:8787`，外网填写归档网站的 HTTPS 根地址；都需填写原连接密码，再点击「验证并保存」。显示「已连接」后即可下载或添加订阅。

当前脚本为 **1.4.6**，本次升级会将旧默认周期一次性改为季度；之后自行选择的周期会保留。HTTP 仅允许 RFC1918 私有 IPv4：`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`，以及本机环回 `localhost`、`127.0.0.1`、`[::1]`。公网 IP 和其他域名仍须使用 HTTPS；脚本不解析域名来判断内网，也不允许仿冒私有 IP 的域名。内网 HTTP 不加密连接密码。切换内外网时，手动修改地址并填写相同的连接密码重新验证；脚本不会自动发现或切换入口。脚本不预设任何站点地址，也不含自动更新链接；更新时从归档网站「连接与扩展」重新安装当前版本。

点击下载后，按钮立即变为深色，并显示提交、排队、解析、已保存数量和已用时间。任务状态每秒更新，短暂断线自动重连，刷新 X 后继续跟踪未完成任务；下载总量未知时不显示虚假的百分比。

连接会在启动和保存时向服务器实际验证，错误密码不会保存。改连接密码后，各浏览器需要重新填写；旧版独立连接仍兼容，可在旧版连接管理里撤销。脚本连接只允许验证、提交和跟踪下载任务，不能读取设置或私人媒体库。新装脚本没有反应时，先确认脚本猫已允许它在 X 上运行，再刷新页面。

## 可选：单帖外网分享

不配置时，复制原帖链接和保存文件仍可使用，独立本站分享页不可创建。

配置 `XARCHIVE_SHARE_ORIGIN=https://share.example.com`，将该独立域名反向代理到归档服务，并保留原始 Host。公开入口只应允许 `GET` / `HEAD` 请求访问 `/s/…` 和 `/static/share.*`、`/static/viewer.*`；其余路径拒绝访问。应用也会按分享域名限制请求。外网管理须使用另一独立 HTTPS 地址并保留网站登录认证，不能复用分享入口放开管理路径。

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
├── gallery_runner.py       # JSON 原子保存适配
├── library.py / migrate.py # 归档索引与旧库导入
├── avatars.py / sharing.py # 头像缓存和单帖分享
├── static/                # 网站与媒体查看器
└── integration/           # ScriptCat 脚本
tests/                     # 自动检查
```

v1.0.6 已通过 **85 项 Python 自动检查**及两组 JavaScript 场景，覆盖统一搜索、历史昵称、真实成功时间、Cookie切换与全局冷却竞态、误报修复和既有任务/调度功能。

安装 `requirements-dev.txt` 后运行：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node --check xarchive/static/app.js
node --check xarchive/integration/xarchive.user.js
node tests/test_task_layout.mjs
node tests/test_scriptcat_feedback.mjs
```

v1.0.5 已通过 **63 项 Python 自动检查**和两组 JavaScript 场景，新增全筛选批量操作、取消/重试竞态、日历周期、季度迁移幂等、暂停保留、日期均衡及自动更新窗口与间隔检查。隔离网页实测跨页处理64项任务无确认，保留归档；NAS副本演练407订阅分散到91天、每天4–5条且两项暂停保留。v1.0.3的41项基础检查和v1.0.4私有IPv4/HTTPS边界检查继续保留，覆盖队列恢复、限流回队、写库重试、原子保存、权限、代理与连接密码、正文上限、导入及分享。此前已在真实 X / ScriptCat / NAS 链路验证新图帖、新视频帖落盘及网页播放，重复更新没有新增媒体文件。历史实测不保证所有账号、帖子与平台状态，本版未重测长期追更或长视频。

**已知边界**：X 限流、登录状态、账号权限和搜索返回范围会影响完整性；不保证下载不可见或已删除内容。当前不采集评论、转帖、引用帖和文章；大仓库仍按博主扫描索引。手机仅做模拟尺寸检查；长期自动计划、长视频及 Chrome 中用户自设新密码的完整真实下载链路未全面验证。HTTPS 入口需要单独配置并验证，本次升级不会自动加密原有 HTTP 管理连接。

本项目用于保存自己有权访问的内容。发布包不包含运行数据库、Cookie、下载媒体、本机工作记录或旧项目 Git 历史。
