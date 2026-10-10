# Blackboard 内部细节（bb-fetch 参考）

> 这些是实测踩出来的，不是文档抄的。改代码前先读这篇。

## 1. 有两套 REST API，别搞混

| | `/learn/api/public/v1/...` | `/learn/api/v1/...` |
|---|---|---|
| 定位 | **官方** REST API（文档里那套） | **界面自己用**的内部 API |
| 认证 | 设计上要**学校管理员注册应用**（OAuth）才能拿 token | 吃**浏览器会话 cookie** |
| 能否用 cookie 调 | **视站点而定**。实测某校（SHSID, Learn 3900.39.0）**可以**（`/users/me`、`/courses` 返回 200）；但也有站点返回 401 | 一般可以 |

**结论**：不要把「官方 API 需要管理员注册」当成绝对规则 —— 很多站点对**已登录会话**是放行 public/v1 的。
配置向导的体检会实测，别猜。

### 常用端点（带会话 cookie 即可）

```
GET /learn/api/public/v1/system/version                 # 判断是不是 BB、版本号（无需登录）
GET /learn/api/public/v1/users/me                       # 当前用户（拿 id）
GET /learn/api/public/v1/users/{uid}/courses?expand=course&limit=200
                                                        # 选课列表（含 course.name / course.availability）
GET /learn/api/public/v1/courses/{cid}/contents?limit=200
GET /learn/api/public/v1/courses/{cid}/contents/{id}/children
GET /learn/api/public/v1/courses/{cid}/announcements
GET /learn/api/public/v1/courses/{cid}/gradebook/columns
GET /learn/api/public/v1/courses/{cid}/gradebook/users/{uid}
```

- `course.availability.available` 取值 `Yes` / `No` / `Term`（`No` 多为已过期或未开放）。
- **内容树要递归**：`/contents` 顶层常常只返回一个"内容"文件夹，真正的文件在其 `children` 里。

## 2. REST 的内容覆盖 < HTML 抓取（关键结论）

实测同一门物理课：**REST 递归 = 14 个文件，HTML 抓取 = 20 个**。
且 REST **不直接给 `bbcswebdav` 下载直链**。

→ **文件全量与下载** 走 HTML 抓取；REST 只用来**列课程**（和未来的成绩/待办等结构化查询）。

## 3. TLS：为什么必须用 curl

部分站点**只开 TLS 1.2 且只认老式加密套件**（如 `AES256-SHA`），**不开 TLS 1.3**。
Python 3.13 默认安全等级（SECLEVEL=2）禁用了这些老套件 → `SSLV3_ALERT_HANDSHAKE_FAILURE`。

```
curl    --tlsv1.2                    → 200 ✅
python  默认                          → 握手失败 ❌
python  set_ciphers('DEFAULT@SECLEVEL=1') → TLSv1.2 成功 ✅
```

**所以网络层一律走 curl 子进程。** 这也是为什么本技能坚持不引入 requests/httpx。

## 4. 登录页结构

- 表单里有 **nonce**（隐藏字段），必须原样回填。
- 页面上**不止一个 form**（还有语言切换等）→ 必须挑**含 `user_id` 的那个**。
- 成功判据：抓门户 tab 页 `/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1`，
  页面含 **"注销"** 或 **"Sign Out"** 才算真会话（仅看到表单提交成功不算）。
- **SSO 站点**：登录页会跳到别的域名 / 出现 `SAMLRequest`、`Shibboleth`、`ADFS` 等 → 本技能不适用。

## 5. 内容树抓取

```
入口：/webapps/blackboard/execute/launcher?type=Course&id={courseId}&url=
  → 页面里找 href 含 listContent.jsp?course_id={cid}&content_id=_NNN_1   （内容根）
  → 对每个内容根 BFS：解析 <h3> 里的文件夹链接（listContent.jsp / launchLink.jsp）
    以及 <a href> 里的文件链接（正则兜底：bbcswebdav / xid- / 常见扩展名）
```

- 深度上限 6 层、页间 sleep 0.25s（防打爆）。
- **文件名经常是假的**：链接文字写着 "Cambridge Workbook"，实际可能是另一本书。以直链为准。
- 课程若无内容根 → 抛 `EmptyCourse`（**良性**，不算抓取失败）。

## 6. 下载与 cookie 的坑

- BB 会话 cookie 是 **HttpOnly**；curl 写的 cookie jar 会给它加 `#HttpOnly_` 前缀。
- **aria2 会把以 `#` 开头的行当注释跳过** → 直接丢会话、下载 403。
  → `BB.aria2_cookie_file()` 负责剥掉前缀后另存一份给 aria2 用。

## 7. 会话与凭据

- 会话 cookie 只在 jar 里短暂存在；`bb_watch` 每轮跑完即删。
- 凭据（账号密码）存系统凭据库，**配置文件里绝不出现密码**。
