# X归档站

> X Archive Station · 自托管 X/Twitter 媒体归档 + 多平台视频下载 + 本地时间线阅读

## 项目名

本项目名为 **X归档站**（英文 X Archive Station）。核心是一套部署在自己 NAS/服务器上的「抓取 → 存储 → 阅读」闭环：自动抓取 X 博主的媒体与推文全文，再用一个 X 风格的时间线网页来浏览。

## 目录结构

```
x订阅/
├── README.md             ← 本文件（项目名 + 导航）
├── 项目文档.md            ← 完整项目文档（架构 / 部署 / 全部变更 / 操作手册）
├── MeTube/               ← 服务一：多平台视频下载器
│   ├── compose.yaml      ← Docker Compose 编排（含代理）
│   └── 部署教程.md        ← 部署与使用教程
└── GalleryDL-X订阅/       ← 服务二：X 博主自动归档
    ├── Dockerfile        ← 镜像定义（python + gallery-dl）
    ├── compose.yaml      ← 编排（含随机调度）
    ├── config.json       ← gallery-dl 配置（限速 + 推文元数据）
    ├── run.sh            ← 下载执行脚本
    ├── entrypoint.sh     ← 类人随机调度器
    ├── targets.txt       ← 订阅清单（每日随机 1-2 次）
    ├── targets-weekly.txt← 周更清单（每 6-8 天随机一次）
    ├── cookies.txt       ← X 登录 cookies（敏感，勿外传）
    └── Reader/           ← 服务三：X 风格本地阅读网站（纯 Python + 原生 JS，零依赖）
        ├── backend.py    ← 后端（API + 媒体/头像缓存 + 管理接口）
        ├── index.html    ← 前端（X 白底三栏时间线 + 管理设置面板）
        └── compose.yaml  ← 编排（只读挂载数据 + 头像缓存卷 + config 管理）
```

## 三个服务与访问地址

| 服务 | 作用 | 访问 |
|---|---|---|
| MeTube | 手动下载 YouTube 等多平台视频 | http://10.0.0.10:8081 |
| GalleryDL | 自动抓取 X 博主媒体 + 推文全文（无界面） | 无（CLI） |
| X Reader | X 风格时间线浏览归档内容 | http://10.0.0.10:9090 |

## 快速上手

- 完整说明见 `项目文档.md`（涵盖服务器连接、部署、维护、变更日志、风控结论）
- 本目录是**独立工程工作区**，与服务器 `/vol1/1000/Docker/` 下对应目录一一同步：改文件后 scp 上传并重启容器即可
- `cookies.txt` 为敏感凭据，请勿提交到公开仓库
