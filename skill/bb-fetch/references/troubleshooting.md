# bb-fetch 排障

按症状查。每条给出「现象 → 原因 → 处理」。

## 配置阶段（bb_setup.py）

### ❌ 连不上站点
- **现象**：`detect_host_kind` 返回 unreachable / curl code=000。
- **原因**：网址写错；或需校内网/VPN；或站点临时不可用。
- **处理**：核对浏览器官网地址（含 `https://`）；连上校园网/VPN 再试；
  用 `curl -I <网址>` 手动确认 curl 能不能通。

### ❌ 判定"不是 Blackboard"
- **现象**：站点可访问，但不像 BB。
- **原因**：网址指向了门户首页/SSO 页，不是 BB 本体。
- **处理**：用浏览器打开 BB，复制**登录页**所在的域名（一般形如 `xxx.blackboard.com`
  或学校自建域名），再 `--url` 填它。

### ❌ 登录方式：SSO 单点登录（本技能不支持）
- **现象**：向导提示 "SSO 单点登录" 并退出（code 3）。
- **原因**：学校用 Shibboleth / ADFS / 统一身份认证，登录要跳转第三方。
- **处理**：**当前无法用**。这是设计限制（本技能只做表单登录）。
  可反馈给技能作者；或在能拿到**本地账号表单登录页**的前提下改用直连地址。

### ❌ 登录失败（账号密码被拒）
- **现象**：向导 "登录未确认"。
- **原因**：账号/密码错；或该校登录页结构特殊导致回填失败。
- **处理**：先确认能在浏览器登录；注意大小写与前后空格；
  多次失败**不要连试**（可能触发锁定），隔一会儿再试。

## 守望阶段（bb_watch.py）

### 🤫 一直没有任何输出
- **这是正常的**：守望**只在真有新增时才输出**。没有新文件 = 没有输出。
- 想确认它在工作：`python bb_watch.py --status` 看快照时间是否在更新；
  或 `python bb_watch.py --verbose` 看过程日志（stderr）。

### ❌ 日志里出现 `incomplete scan, skip compare`
- **现象**：本轮静默，日志写"抓取不全"。
- **原因**：某门课**真正抓取失败**（网络抖动 / 页面结构变化）。
- **处理**：这是**有意的防误报设计**（宁可漏报一轮，也不误报）。
  偶发不用管；**持续**出现则跑 `--verbose` 看是哪门课、什么错。

### 抓到的文件数明显偏少
- **原因**：课程布局特殊；或该课确实内容少。
- **处理**：对比 `--status` 的科目分布；必要时用浏览器打开该课内容页，看是否有
  非常规挂件（REST 与抓取都覆盖不到的极少数情况）。

## 下载阶段（bbget.py）

### ❌ `非法链接`
- **Git Bash 用户高频**：以 `/bbcswebdav/...` 开头的参数被 MSYS 当成本地路径转换了。
- **处理**：`export MSYS_NO_PATHCONV=1` 后重试；或改用 PowerShell / CMD。

### ❌ 下载 403 / 立刻失败
- **原因**：会话失效，或 aria2 没读到 cookie。
- **处理**：`bbget.py` 会自动重登并生成 aria2 专用 cookie。
  若仍失败，删掉 `~/.bb-fetch/state/cookies*.txt` 再试。

### ❌ `找不到 aria2c`
- **处理**：装 aria2 并确保 `aria2c` 在 PATH；或在 `~/.bb-fetch/config.json` 里
  把 `aria2c` 改成绝对路径（如 `D:/tools/aria2/aria2c.exe`）。

## 通用

### 想看配置/数据在哪
- 用户数据目录：`~/.bb-fetch/`
  - `config.json` —— 站点、课程清单、目录
  - `state/state.json` —— 文件快照
  - `state/bb_watch.log` —— 运行日志
- 凭据：系统凭据库里，service = `bb-fetch`（`python scripts/bb_creds.py show` 看用户名，密码不回显）。

### 想清空重来
```bash
python scripts/bb_creds.py del      # 删凭据
rm -rf ~/.bb-fetch                  # 删配置与快照（谨慎）
python scripts/bb_setup.py          # 重新配置
```
