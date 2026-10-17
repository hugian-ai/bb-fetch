---
name: bb-fetch
slug: bb-fetch
version: 0.1.1
displayName: bb-fetch
summary: 守望学校 Blackboard 课程更新，点名下载课件与作业；凭据进系统凭据库，脚本零第三方依赖。
description: 从学校 Blackboard（BB）把课程资料"取回来"：守望课程内容与公告的新增、只在真更新时生成可读提醒，并按需多线程下载课件/作业。触发词：Blackboard、BB、学校课程平台、课程资料、课件、讲义、公告、校历、"盯更新"、"有新资料通知我"、"把某个文件下下来"、"把课程资料同步到本地"，也用于配置本校 BB 账号、诊断 BB 连通性。
description_en: Watch a school Blackboard (Bb) site for new course content/announcements and download course files; use when the user mentions Blackboard, school course portal, course materials, lecture slides, homework, or wants to be notified of new material.
license: MIT
homepage: https://github.com/hugian-ai/bb-fetch
tags: [blackboard, education, course-materials, download, watcher]
agent_created: true
---

# bb-fetch

把 Blackboard 上的课程资料**取回来**：一边**守望**（有新内容才提醒），一边**下载**（点名制，多线程）。

> **凭据零写盘**：账号密码存进系统凭据库（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service），
> 配置文件里**不含**任何密码。首次使用需运行配置向导。

## 何时用这个技能

- 用户说"帮我盯着 Blackboard / BB / 学校课程平台"、"有新课件通知我"
- 用户给出某个 `/bbcswebdav/...` 链接或课程文件链接，要下载
- 用户要配置/诊断本校 Blackboard 访问（能不能连、登录方式是不是支持）

## 前置条件

| 项 | 要求 |
|---|---|
| Python | 3.8+（脚本本身**零第三方依赖**，用系统自带库即可） |
| curl | 系统自带（Windows 10+ / macOS / Linux 均有） |
| aria2c | 仅"下载"功能需要（`aria2c` 在 PATH，或配置里给绝对路径） |
| 网络 | 能访问学校 BB 站点（可能需要校内网/VPN） |

## 一、首次配置（必做一次）

```bash
python scripts/bb_setup.py
```

向导会：① 确认站点是不是 Blackboard、能不能连上 → ② 保存凭据到系统凭据库 →
③ **自检登录方式** → ④ 列出你账号下的课程，让你勾选要盯的几门 → 写入配置。

- **推荐交互式运行**（用户看着屏幕输入账号密码，密码不回显）。
- 若由 Agent 代跑（非交互）：`python scripts/bb_setup.py --url <网址> --user <账号> --pass <密码> --courses auto --yes`
  —— 但**不要**在对话里索取/传递密码；让用户自己运行，或先用 `--check` 只做体检。
- 只体检不改动：`python scripts/bb_setup.py --check`
- 只列出课程：`python scripts/bb_setup.py --list`
- 换学期/换课：`python scripts/bb_setup.py --refresh`

配置与快照默认存 `~/.bb-fetch/`（**与技能目录分离**，故技能更新不会覆盖你的数据）。

> **小技巧**：课程名默认用 BB 里的官方全名，可能很长。想更易读，可在 `~/.bb-fetch/config.json`
> 的每门课加一个 `"label": "IB物理 HL"` —— 提醒里就会用这个短名。

## 二、守望（核心）

```bash
python scripts/bb_watch.py --baseline   # 首次：建立基线快照
python scripts/bb_watch.py              # 之后每轮；只在有新增时输出到 stdout
python scripts/bb_watch.py --status     # 看当前快照概况（不联网）
```

**输出纪律（重要）**：无变化 / 抓取不全 / 出错 → **stdout 为空**。只有真新增才输出提醒文本。
所以调用方只需判断「stdout 是否非空」：非空 = 有更新，转发给用户即可。

> 建议做成**定时自动化**（如每 30 分钟一轮）。技能只负责产出提醒文本，推送渠道由调用方决定。

## 三、下载

```bash
python scripts/bbget.py "/bbcswebdav/pid-.../xid-..." --dir "~/Downloads/bb" --out "讲义.pdf"
```

- 不传 `--dir` 则落到配置里的 `download_dir`。
- **Git Bash 用户**：传 `/bbcswebdav/...` 前先 `export MSYS_NO_PATHCONV=1`（否则被当本地路径转换）。
- 默认 16 线程；实测 4.7MB PDF 秒级完成。

## 常见问题（详见 references/troubleshooting.md）

- **连不上 / TLS 握手失败**：本站可能只开 TLS1.2 老套件 —— 本技能走 curl，通常没问题；纯 Python 客户端才会失败。
- **登录方式不支持**：站点若走 **SSO 单点登录**（Shibboleth / ADFS / 统一身份认证），本技能用不了，向导会明确告知。
- **抓到的文件不全**：BB 里文件名有时与链接不一致；以快照里的直链为准。

## 设计说明（供维护/二次开发）

- 传输层：**curl 子进程**（避开 Python TLS 指纹被拒）。
- 登录：找含 `user_id` 的表单，回填 + nonce，`curl` POST，用门户页"注销"标记校验会话。
- 抓取：`launcher → listContent.jsp` 内容树 **BFS**，比界面更全，并能拿到 `bbcswebdav` 直链。
- 凭据：`bb_creds.py` 跨平台适配（Windows ctypes / macOS `security` / Linux `secret-tool` / keyring / 文件兜底）。
- 内部细节（两套 REST API、Classic vs Ultra 等）：见 `references/blackboard-internals.md`。
