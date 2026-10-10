# bb-fetch

**把学校 Blackboard 上的课程资料取回来** —— 自动守望新内容，按需多线程下载。

> 给所有需要盯 Blackboard 的学生：老师什么时候传了新课件、发了新公告，第一时间知道；
> 想要的讲义作业，一条命令下到本地。
> 自带配置向导，**填自己的账号就能用**，凭据只进系统凭据库，不落明文。

## 它解决什么问题

Blackboard（BB）是很多学校在用的课程平台。它的痛点是：**你不主动去看，就永远不知道老师又传了什么。**
bb-fetch 把这件事自动化：

- 🔔 **守望**：每隔一段时间自动扫一遍你的课程内容与公告 → **只有真新增才提醒**（不刷屏、不误报）
- 📥 **下载**：给出文件链接就能多线程下载（16 连接，实测 4.7 MB 秒级完成）
- 🔐 **凭据零写盘**：账号密码存进系统凭据库（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service）

## 特点

- **零第三方依赖**：只用 Python 系统库 + `curl`。不装 requests、不装 httpx、不装 Selenium。
- **能过"连不上"的坎**：部分 BB 站点只开 TLS 1.2 老套件，Python 默认握手会失败 —— 本工具走 `curl`，天然兼容。
- **抓得比界面全**：走内容树 BFS，藏在折叠文件夹里、"加载更多"后面的文件也能拿到下载直链。
- **自己会体检**：配置向导先测站点可达性、再测登录方式，SSO 站点会**明确告诉你用不了**，而不是静默失败。

## 安装

### 作为 WorkBuddy 技能（推荐）

在 WorkBuddy 的技能市场搜索 **bb-fetch** 安装；或手动把 `skill/bb-fetch/` 整个目录
拷到 `~/.workbuddy/skills/bb-fetch/`。

### 手动使用（任何 Agent / 命令行）

```bash
git clone <repo-url> bb-fetch
cd bb-fetch/skill/bb-fetch
```

## 快速开始

```bash
# 1) 一次性配置：填站点、存凭据、自检登录、勾选要盯的课
python scripts/bb_setup.py

# 2) 建立首次快照
python scripts/bb_watch.py --baseline

# 3) 之后每轮（建议挂成定时任务）
python scripts/bb_watch.py          # stdout 为空 = 没更新；非空 = 有更新，转发给用户

# 4) 下载某个文件
python scripts/bbget.py "/bbcswebdav/pid-.../xid-..." --dir ~/Downloads/bb
```

配置和数据都在 `~/.bb-fetch/`（**与代码分离**，更新技能不会动你的数据）。

## 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.8+ |
| curl | 系统自带即可 |
| aria2c | 仅下载功能需要（`aria2c` 在 PATH，或配置里写绝对路径） |
| 网络 | 能访问你学校的 BB（可能需校内网 / VPN） |

## 已知限制（诚实说）

- **只支持表单登录**。学校若用 **SSO 单点登录**（Shibboleth / ADFS / 统一身份认证），本工具用不了。
- **只报增，不报删/改**：文件被删或改名不会提醒。
- 不同学校的 BB 页面结构可能有差异，个别站点需要微调抓取规则。
- 请**只用于本人账号**，遵守你所在学校的平台条款。

## 项目结构

```
bb-fetch/                     ← 仓库根
├── skill/bb-fetch/           ← 技能包本体（可安装 / 可上架）
│   ├── SKILL.md
│   ├── scripts/              bb_setup · bb_watch · bbget · bb_core · bb_creds
│   ├── references/           Blackboard 内幕 · 排障
│   └── assets/               config.example.json
├── tools/build.py            ← 校验 + 打包
└── dist/                     ← 打包产物 bb-fetch.zip
```

## 开发

```bash
python tools/build.py            # 校验并打包到 dist/bb-fetch.zip
python tools/build.py --check    # 只校验
```

单测式自检（需要能访问一个 BB 站点）：

```bash
python skill/bb-fetch/scripts/bb_setup.py --check        # 站点与登录方式体检
python skill/bb-fetch/scripts/bb_watch.py --status        # 看快照
```

## License

[MIT](LICENSE)
