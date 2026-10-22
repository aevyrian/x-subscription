# 脚本与网站共用接口 v1

网站使用现有 HttpOnly 登录会话。ScriptCat 使用 `Authorization: Bearer <独立连接凭据>`，凭据从网站生成，不放在 URL。

| 方法与路径 | 用途 |
| --- | --- |
| POST `/api/v1/tasks` | 提交一次下载或订阅 |
| GET `/api/v1/tasks` | 查询共享任务列表，不包含私密配置和原始日志 |
| GET `/api/v1/tasks/{job_id}` | 查询单项任务状态 |
| GET `/api/v1/library?url=…` | 查询链接对应内容是否已归档，url 需要 URL 编码 |
| GET / POST `/api/admin/clients` | 列出 / 生成脚本连接，仅网站登录会话可调用 |
| DELETE `/api/admin/clients/{id}` | 撤销连接，仅网站登录会话可调用 |

提交示例：

```json
{"url":"https://x.com/exampleuser/status/2001","mode":"download","limit":20}
```

`mode` 支持 `download` 和 `subscribe`。下载模式支持 X 博主主页、media 页或单帖，以及 @用户名；仅接受明确的 X/Twitter 主机。博主范围为 1–1500 的整数，默认 20，单帖忽略范围。订阅模式要求博主链接，`frequency` 可选 daily、weekly、monthly，默认 weekly。已有订阅返回 duplicate，不擅自更改频率或暂停状态。

新建返回 201；已有相同目标的等待/执行任务返回 200 和 `duplicate:true`。任务内包含 job_id、status、user、tweet_id、archive_path、提交/开始/完成时间和最终新增计数。状态为 queued / running / done / failed；未开始或未完成的计数可能为 null。无新增内容是 done 且新增 0。

错误：401 身份无效，400 参数错误，413 请求体过大，404 接口或任务不存在，503 存储暂不可用。提交只表示持久化排队成功，执行结果必须查询任务。错误字段对脚本返回通用说明，详细脱敏日志需登录网站查看。

归档链接采用 `/?user=用户名` 和 `/#tweet=帖子ID`。网站和脚本复用同一任务存储、执行器与文件目录；脚本不直接访问文件系统。现有 `/api/admin/*` 与阅读 API 保留供原界面使用。
