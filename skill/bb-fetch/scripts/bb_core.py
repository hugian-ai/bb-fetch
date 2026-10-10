# -*- coding: utf-8 -*-
"""bb_core.py — Blackboard 核心库（bb-fetch）

设计要点（均为实战验证）：
  · 网络层一律走 curl 子进程 —— 部分 BB 站点只开 TLS1.2 + 老套件，Python 默认握手失败。
  · 表单登录（自动找含 user_id 的表单并回填），非交互，可无人值守。
  · 内容树 BFS（listContent.jsp）—— 抓得比界面还全，且能拿到下载直链。
  · 凭据走系统凭据库（bb_creds，跨平台，优先零依赖）。

用户数据（config / state）默认放在 `~/.bb-fetch/`，与技能目录分离 ——
这样技能更新/重装不会覆盖你的配置与快照。
"""
import html as htmllib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bb_creds  # noqa: E402

HOME_DIR = os.path.join(os.path.expanduser("~"), ".bb-fetch")
DEFAULT_CONFIG = os.path.join(HOME_DIR, "config.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

FILE_RE = re.compile(
    r"(\.pdf|\.docx?|\.xlsx?|\.pptx?|\.mp4|\.mp3|\.zip|\.png|\.jpe?g|"
    r"\.gif|\.txt|\.csv|\.ipynb|\.py|\.java|\.rar|\.7z|\.mov|\.webm|"
    r"bbcswebdav|xid-)", re.I)

MAX_DEPTH = 6
SLEEP_BETWEEN = 0.25


class EmptyCourse(Exception):
    """课程无内容根（老师未挂内容 / 布局为空）—— 良性，不算抓取失败。"""


class SSONotSupported(Exception):
    """站点走 SSO 单点登录，本技能的表单登录方式不适用。"""


# ────────────────────────────── 配置 ──────────────────────────────
def resolve_config_path(explicit=None):
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("BB_CONFIG"):
        cands.append(os.environ["BB_CONFIG"])
    cands.append(DEFAULT_CONFIG)
    cands.append(os.path.join(SKILL_DIR, "config.json"))
    for c in cands:
        c = os.path.expanduser(c)
        if os.path.exists(c):
            return c
    return DEFAULT_CONFIG


DEFAULT_CONFIG_TEMPLATE = {
    "base_url": "",
    "credential_service": "bb-fetch",
    "state_dir": "state",
    "download_dir": "~/Downloads/bb-fetch",
    "aria2c": "aria2c",
    "courses": [],
    "course_discovery": {"enabled": False},
}


def load_config(path=None):
    """读取配置；文件不存在时返回含默认值的模板（不写盘）。"""
    p = resolve_config_path(path)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG_TEMPLATE))
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["_path"] = p
    cfg["base_url"] = (cfg.get("base_url") or "").rstrip("/")
    # 相对路径 → 相对配置文件所在目录；支持 ~ 展开
    base_dir = os.path.dirname(os.path.abspath(p))
    for k in ("state_dir", "download_dir"):
        v = cfg.get(k) or ""
        v = os.path.expanduser(v)
        if v and not os.path.isabs(v):
            v = os.path.join(base_dir, v)
        cfg[k] = os.path.normpath(v) if v else ""
    cfg.setdefault("state_dir", os.path.join(HOME_DIR, "state"))
    cfg.setdefault("download_dir", os.path.join(os.path.expanduser("~"), "Downloads", "bb-fetch"))
    return cfg


def save_config(cfg, path=None):
    p = os.path.expanduser(path or cfg.get("_path") or DEFAULT_CONFIG)
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return p


def get_creds(cfg):
    """返回 (user, pw, base)。凭据优先级：env → 系统凭据库。"""
    svc = cfg.get("credential_service", bb_creds.DEFAULT_SERVICE)
    user, pw = bb_creds.load(svc)
    base = (os.environ.get("BB_URL") or cfg.get("base_url") or "").rstrip("/")
    return user, pw, base


# ────────────────────────────── curl 传输层 ──────────────────────────────
def strip_tags(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def norm(u):
    u = htmllib.unescape(u)
    p = urllib.parse.urlsplit(u)
    return p.path + ("?" + p.query if p.query else "")


class BB:
    """基于 curl 子进程的会话客户端（cookie jar 持久化）。"""

    def __init__(self, base, jar):
        self.base = base.rstrip("/")
        self.jar = jar
        self.tmp = os.path.join(os.path.dirname(jar), "tmp")
        os.makedirs(self.tmp, exist_ok=True)

    def _curl(self, extra):
        return subprocess.run(["curl", "-s", "-L", "--max-time", "45", "-A", UA,
                               "-b", self.jar, "-c", self.jar,
                               "-H", "Referer: " + self.base + "/"] + extra,
                              capture_output=True, timeout=70)

    def get(self, path, tries=3, save=None):
        url = path if path.startswith("http") else self.base + path
        body = save or os.path.join(self.tmp, "resp.html")
        last = ""
        for i in range(tries):
            try:
                out = self._curl(["-o", body, "-w", "%{http_code}|%{url_effective}", url])
                meta = out.stdout.decode("ascii", "replace").strip()
                code, final = (meta.split("|", 1) + [""])[:2]
                if code == "200":
                    txt = None
                    if save is None and os.path.exists(body):
                        txt = open(body, encoding="utf-8", errors="replace").read()
                    return code, final, txt
                last = "HTTP " + code
                time.sleep(1 + i)
            except Exception as e:  # noqa: BLE001
                last = str(e)[:120]
                time.sleep(1 + i)
        return "000", url, None

    def get_json(self, path, params=None, tries=2):
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        body = os.path.join(self.tmp, "api.json")
        last = ""
        for i in range(tries):
            try:
                out = self._curl(["-H", "Accept: application/json",
                                  "-o", body, "-w", "%{http_code}", url])
                code = out.stdout.decode("ascii", "replace").strip()
                if code == "200" and os.path.exists(body):
                    with open(body, encoding="utf-8", errors="replace") as f:
                        return code, json.load(f)
                last = "HTTP " + code
                time.sleep(1 + i)
            except Exception as e:  # noqa: BLE001
                last = str(e)[:120]
                time.sleep(1 + i)
        return last, None

    def post(self, path, payload):
        url = path if path.startswith("http") else self.base + path
        data_f = os.path.join(self.tmp, "post.txt")
        body = os.path.join(self.tmp, "resp.html")
        with open(data_f, "w", encoding="utf-8") as f:
            f.write(urllib.parse.urlencode(payload))
        out = self._curl(["-H", "Content-Type: application/x-www-form-urlencoded",
                          "--data", "@" + data_f, "-o", body,
                          "-w", "%{http_code}|%{url_effective}", url])
        meta = out.stdout.decode("ascii", "replace").strip()
        code, final = (meta.split("|", 1) + [""])[:2]
        txt = open(body, encoding="utf-8", errors="replace").read() if os.path.exists(body) else ""
        return code, final, txt

    # --- 会话 ---
    def session_alive(self):
        _, _, portal = self.get("/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1")
        return bool(portal) and ("注销" in portal or "Sign Out" in portal)

    def login(self, user, pw):
        if os.path.exists(self.jar):
            try:
                os.remove(self.jar)
            except OSError:
                pass
        _, _, page = self.get("/webapps/login/")
        if not page:
            raise RuntimeError("登录页取不到")
        form = None
        for f in re.findall(r"<form[^>]*>.*?</form>", page, re.S | re.I):
            if "user_id" in f:
                form = f
                break
        if form is None:
            raise SSONotSupported("登录页没有本地账号表单（user_id）——本站可能用 SSO 单点登录，"
                                  "本技能暂不支持")
        payload = {}
        for tag in re.findall(r"<input[^>]*>", form, re.I):
            nm = re.search(r'name="([^"]*)"', tag)
            vl = re.search(r'value="([^"]*)"', tag)
            if nm and nm.group(1):
                payload[nm.group(1)] = vl.group(1) if vl else ""
        payload["user_id"] = user
        payload["password"] = pw
        act = re.search(r'<form[^>]*action="([^"]*)"', form, re.I)
        action = act.group(1) if act else "/webapps/login/"
        if not action.startswith(("/", "http")):
            action = "/" + action
        self.post(action, payload)
        if not self.session_alive():
            raise RuntimeError("登录未确认（门户页无注销标记）——请核对账号密码")
        return True

    def ensure_login(self, user, pw):
        if not self.session_alive():
            self.login(user, pw)
        return True

    def aria2_cookie_file(self, dest=None):
        """转成 aria2 可读 cookie（剥离 #HttpOnly_ 前缀）。"""
        dest = dest or os.path.join(os.path.dirname(self.jar), "cookies_aria.txt")
        lines = open(self.jar, encoding="utf-8").read().splitlines()
        out = []
        for ln in lines:
            if ln.startswith("#HttpOnly_"):
                ln = ln[len("#HttpOnly_"):]
            if ln.startswith("#") and not (ln.startswith("# Netscape")
                                           or ln.startswith("# http")):
                continue
            out.append(ln)
        with open(dest, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return dest


# ────────────────────────────── 站点自检 ──────────────────────────────
def detect_host_kind(bb):
    """判断是不是 Blackboard，且能否用 curl 连上。
    返回 (ok, kind, detail)：kind ∈ {'blackboard','unknown','unreachable'}
    """
    code, _, _ = bb.get("/learn/api/public/v1/system/version")
    if code == "200":
        return True, "blackboard", "REST /system/version 可访问"
    code2, final, page = bb.get("/webapps/login/")
    if code2 == "200" and page is not None:
        low = page.lower()
        if "blackboard" in low or "user_id" in low:
            return True, "blackboard", "经典登录页可访问"
        return True, "unknown", "站点可访问，但不像 Blackboard"
    return False, "unreachable", "连不上（curl code=%s）——检查网址或网络" % (code or code2)


def detect_login(bb):
    """返回 (kind, detail)：'form' | 'sso' | 'unknown'。"""
    code, final, page = bb.get("/webapps/login/")
    if not page:
        return "unknown", "登录页取不到 (code=%s)" % code
    for f in re.findall(r"<form[^>]*>.*?</form>", page, re.S | re.I):
        if "user_id" in f:
            return "form", "检测到本地账号表单（user_id）→ 本技能支持"
    low = page.lower()
    markers = ("samlrequest", "shibboleth", "adfs", "oauth", "openid", "wayf",
               "single sign", "身份认证", "统一身份")
    same_host = urllib.parse.urlsplit(final).hostname == urllib.parse.urlsplit(bb.base).hostname
    if any(m in low for m in markers) or not same_host:
        return "sso", "疑似 SSO 单点登录（落到 %s）" % final[:90]
    return "unknown", "未识别登录方式（落到 %s）" % final[:90]


# ────────────────────────────── 课程 ──────────────────────────────
def all_courses(bb):
    """列出账号下全部课程（REST）。返回 [{'id','name','available'}...]。"""
    _, me = bb.get_json("/learn/api/public/v1/users/me")
    uid = (me or {}).get("id")
    if not uid:
        return []
    _, mem = bb.get_json("/learn/api/public/v1/users/%s/courses" % uid,
                         {"limit": 200, "expand": "course"})
    res = (mem or {}).get("results", []) if isinstance(mem, dict) else []
    out = []
    for m in res:
        cid = m.get("courseId")
        co = m.get("course") or {}
        if not cid:
            continue
        out.append({
            "id": cid,
            "name": re.sub(r"\s+", " ", co.get("name") or "").strip(),
            "available": (co.get("availability") or {}).get("available"),
            "lastAccessed": m.get("lastAccessed"),
        })
    return out


def courses_from_config(cfg):
    """读 config.courses。显示名优先用 label（可选短名），否则用 name。"""
    out = []
    for c in cfg.get("courses", []):
        if not c.get("id"):
            continue
        out.append({
            "name": c.get("label") or c.get("name") or c["id"],
            "id": c["id"],
            "area": c.get("area", ""),
        })
    return out


def discover_courses(bb, cfg):
    """按 config.course_discovery 规则自动筛课（可选）。
    未启用或筛不出 → 回退 config.courses。
    """
    fallback = courses_from_config(cfg)
    disc = cfg.get("course_discovery", {}) or {}
    if not disc.get("enabled", False):
        return fallback
    inc = re.compile(disc.get("include", "."), re.I)
    exc = re.compile(disc.get("exclude", r"(?!x)x"), re.I)
    grade = disc.get("grade_marker", "")
    meta = {c["id"]: c for c in fallback}
    found = []
    for c in all_courses(bb):
        if c["available"] not in (None, "Yes", "Term"):
            continue
        if grade and grade not in c["name"]:
            continue
        if exc.search(c["name"]) or not inc.search(c["name"]):
            continue
        known = meta.get(c["id"], {})
        found.append({"name": known.get("name") or c["name"], "id": c["id"],
                      "area": known.get("area", "")})
    return found or fallback


# ────────────────────────────── 抓取 ──────────────────────────────
def extract_items(page_html, resp_url):
    folders, files = [], []
    for m in re.finditer(r"<h3[^>]*>(.*?)</h3>", page_html, re.S):
        block = m.group(1)
        hm = re.search(
            r'href="([^"]*(?:listContent\.jsp\?[^"]*content_id=_\d+_1'
            r'|launchLink\.jsp\?[^"]*toc_id=_\d+_1)[^"]*)"', block, re.I)
        if not hm:
            continue
        title = strip_tags(re.sub(r"<a.*?</a>", lambda a: strip_tags(a.group(0)),
                                  block, flags=re.S))
        folders.append((urllib.parse.urljoin(resp_url, hm.group(1)), title[:120] or "(untitled)"))
    for hm in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page_html, re.S | re.I):
        href, text = hm.group(1), strip_tags(hm.group(2))
        if href.lower().startswith("javascript"):
            continue
        if "listContent.jsp" in href or "launchLink.jsp" in href:
            continue
        if not FILE_RE.search(href):
            continue
        files.append((urllib.parse.urljoin(resp_url, href), text[:150] or "(untitled)"))
    return folders, files


def crawl_course(bb, cid):
    """BFS 内容树；返回 {norm_url: title}。"""
    code, rurl, page = bb.get("/webapps/blackboard/execute/launcher?type=Course&id=%s&url=" % cid)
    if not page:
        raise RuntimeError("launcher 取不到 (code=%s)" % code)
    roots = []
    for m in re.finditer(
            r'href="([^"]*listContent\.jsp\?course_id=%s(?:&amp;|&)content_id=_\d+_1[^"]*)"'
            % re.escape(cid), page, re.I):
        n = norm(m.group(1))
        if n not in roots:
            roots.append(n)
    if not roots:
        raise EmptyCourse("无内容根 (课程可能未挂内容或布局变更)")
    files, seen = {}, set()
    queue, depth = list(roots), {r: 0 for r in roots}
    while queue:
        path = queue.pop(0)
        if path in seen or depth.get(path, 0) > MAX_DEPTH:
            continue
        seen.add(path)
        _, rurl2, html = bb.get(path)
        if not html:
            continue
        fol, fl = extract_items(html, rurl2)
        for u, t in fl:
            files.setdefault(norm(u), t)
        for u, _t in fol:
            np_ = norm(u)
            if np_ not in seen and np_ not in depth:
                depth[np_] = depth.get(path, 0) + 1
                queue.append(np_)
        time.sleep(SLEEP_BETWEEN)
    return files


def course_announcements(bb, cid):
    _, rurl, page = bb.get(
        "/webapps/blackboard/execute/announcement?method=search&context=mybb&course_id=%s&viewChoice=2" % cid)
    anns = {}
    if not page:
        return anns
    for hm in re.finditer(r'<a[^>]+href="([^"]*announcement[^"]*)"[^>]*>(.*?)</a>', page, re.S | re.I):
        href, text = hm.group(1), strip_tags(hm.group(2))
        if not text or len(text) < 4 or "更多公告" in text or "RSS" in text.upper():
            continue
        key = norm(urllib.parse.urljoin(rurl, href))
        anns.setdefault(key, text[:120])
    return anns


def crawl_all(bb, courses, progress=None):
    """抓全部课程；返回 (files, anns, failed, empty)。
    failed 非空 → 调用方应作废本轮（防误报）。"""
    all_files, all_anns, failed, empty = {}, {}, [], []
    for i, c in enumerate(courses, 1):
        cname, cid = c["name"], c["id"]
        if progress:
            progress(i, len(courses), cname)
        try:
            for u, t in crawl_course(bb, cid).items():
                all_files[u] = {"c": cname, "t": t}
            for k, v in course_announcements(bb, cid).items():
                all_anns[k] = {"c": cname, "t": v}
            time.sleep(0.4)
        except EmptyCourse as e:
            empty.append("%s: %s" % (cname, str(e)[:80]))
        except Exception as e:  # noqa: BLE001
            failed.append("%s: %s" % (cname, str(e)[:100]))
    return all_files, all_anns, failed, empty
