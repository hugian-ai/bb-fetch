# -*- coding: utf-8 -*-
"""bb_setup.py — bb-fetch 一次性配置向导

做四件事：
  1. 确认站点地址（并自检是不是 Blackboard、curl 能否连上）
  2. 保存凭据到系统凭据库
  3. 自检登录方式（本地表单 ✅ / SSO ❌ 明确告知）
  4. 列出你账号下的课程，让你勾选要盯的几门 → 写 config.json

用法：
  python bb_setup.py                  # 交互式（推荐）
  python bb_setup.py --check          # 只体检，不写配置
  python bb_setup.py --refresh        # 站点与凭据不变，只重选课程
  python bb_setup.py --url U --user X --pass Y --courses auto --yes   # 非交互
  python bb_setup.py --list           # 只列出课程，不改动

凭据只进系统凭据库（Windows 凭据管理器 / macOS 钥匙串 / …），不写进配置文件。
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bb_core  # noqa: E402
import bb_creds  # noqa: E402


def hr(t=""):
    print(("\n=== %s ===" % t) if t else "")


def ok(m):
    print("  ✅ " + m)


def bad(m):
    print("  ❌ " + m)


def warn(m):
    print("  ⚠️  " + m)


def ask(prompt, default=None):
    d = (" [%s]" % default) if default else ""
    try:
        s = input("%s%s: " % (prompt, d)).strip()
    except EOFError:
        s = ""
    return s or (default or "")


def parse_sel(s, n):
    s = (s or "").strip().lower()
    if s in ("all", "a", "*", "全部"):
        return list(range(n))
    if s in ("none", "n", "q", "", "取消"):
        return []
    out = set()
    for part in re.split(r"[,\s，]+", s):
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                out.update(range(int(a) - 1, int(b)))
            except ValueError:
                continue
        else:
            try:
                out.add(int(part) - 1)
            except ValueError:
                continue
    return sorted(i for i in out if 0 <= i < n)


def _recent(course, days=180):
    """最近 days 天内访问过（lastAccessed 缺失时视为未知→通过）。"""
    la = course.get("lastAccessed")
    if not la:
        return True
    try:
        import datetime
        t = datetime.datetime.fromisoformat(str(la).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - t).days <= days
    except Exception:
        return True


def suggest(courses):
    """自动建议：当前可用 + 近半年访问过（这才是你真正在上的课）。"""
    return [i for i, c in enumerate(courses)
            if c.get("available") in (None, "Yes", "Term") and _recent(c)]


def main():
    ap = argparse.ArgumentParser(description="bb-fetch 配置向导")
    ap.add_argument("--url", help="Blackboard 站点地址，如 https://bb.example.edu")
    ap.add_argument("--user")
    ap.add_argument("--pass", dest="password")
    ap.add_argument("--service", help="凭据库里的 service 名（默认 bb-fetch）")
    ap.add_argument("--courses", help="选课：auto | all | 如 1,3,5-7")
    ap.add_argument("--download-dir")
    ap.add_argument("--state-dir")
    ap.add_argument("--config")
    ap.add_argument("--yes", action="store_true", help="非交互，直接采用建议值")
    ap.add_argument("--check", action="store_true", help="只体检，不写配置")
    ap.add_argument("--list", action="store_true", help="只列出课程，不改动")
    ap.add_argument("--refresh", action="store_true", help="沿用已存站点/凭据，只重选课程")
    args = ap.parse_args()

    interactive = (not args.yes) and sys.stdin.isatty()
    cfg = bb_core.load_config(args.config)
    if args.service:
        cfg["credential_service"] = args.service
    svc = cfg.get("credential_service", "bb-fetch")

    hr("bb-fetch 配置向导")
    print("  用户数据目录: %s" % bb_core.HOME_DIR)
    print("  配置文件:     %s" % cfg["_path"])

    # ── 1. 站点地址 ──────────────────────────────────────────────
    hr("1/4 站点地址")
    url = (args.url or os.environ.get("BB_URL") or cfg.get("base_url") or "").strip()
    if not url and interactive:
        url = ask("你的 Blackboard 网址（浏览器地址栏里那个）")
    if not url:
        bad("没有站点地址。用 --url 指定，或交互式运行。")
        return 2
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    url = url.rstrip("/")
    cfg["base_url"] = url
    print("  站点: %s" % url)

    probe = bb_core.BB(url, os.path.join(bb_core.HOME_DIR, "_setup_cookies.txt"))
    is_bb, kind, detail = bb_core.detect_host_kind(probe)
    if not is_bb:
        bad(detail)
        warn("如果确实是 Blackboard，但这里连不上：常见原因是站点只开 TLS1.2 —— 本技能走 curl，")
        warn("若 curl 也连不上，请确认网址无误、或处于校内网络/VPN。")
        return 2
    ok("站点可达（%s）" % detail)

    login_kind, login_detail = bb_core.detect_login(probe)
    if login_kind == "form":
        ok("登录方式：本地账号表单 —— 本技能支持")
    elif login_kind == "sso":
        bad("登录方式：SSO 单点登录 —— 本技能不支持")
        warn(login_detail)
        warn("说明：本技能用「账号密码表单登录」。你的学校若统一走 SSO（如 Shibboleth/ADFS/企业身份），")
        warn("curl 无法完成跳转认证，暂不可用。可以把这条反馈给技能作者。")
        return 3
    else:
        warn("登录方式未完全确认：%s" % login_detail)

    if args.check:
        hr("体检完成（--check，未写任何配置）")
        return 0

    # ── 2. 凭据 ──────────────────────────────────────────────────
    hr("2/4 凭据")
    user = args.user or os.environ.get("BB_USER")
    pw = args.password or os.environ.get("BB_PASS")
    if not (user and pw) and args.refresh:
        user, pw = bb_creds.load(svc)
    if not (user and pw):
        if interactive:
            user = user or ask("账号（学号/用户名）")
            if not pw:
                import getpass
                pw = getpass.getpass("密码（输入时不显示）: ")
        else:
            bad("缺少凭据。用 --user/--pass，或交互式运行。")
            return 2
    if not (user and pw):
        bad("账号或密码为空。")
        return 2

    used = bb_creds.store(user, pw, svc)
    ok("凭据已存入系统凭据库（后端=%s，service=%s）" % (used, svc))
    print("     （凭据不会写进任何配置文件；换机器需重新运行本向导）")

    # ── 3. 登录自检 ──────────────────────────────────────────────
    hr("3/4 登录自检")
    jar = os.path.join(bb_core.HOME_DIR, "_setup_cookies.txt")
    bb = bb_core.BB(url, jar)
    try:
        if os.path.exists(jar):
            os.remove(jar)
        bb.login(user, pw)
        ok("登录成功，拿到有效会话")
    except Exception as e:  # noqa: BLE001
        bad("登录失败：%s" % str(e)[:160])
        warn("请检查账号密码；若确定无误，可能是站点登录页结构特殊。")
        return 4

    # ── 4. 选课 ──────────────────────────────────────────────────
    hr("4/4 选择要盯的课程")
    courses = bb_core.all_courses(bb)
    if not courses:
        warn("没能列出课程（REST 接口无返回）。你可以稍后重跑 --refresh。")
        courses = []
    if courses:
        sug = set(suggest(courses))
        print("  你的账号下共 %d 门课（★=当前可用且近期访问过，建议勾选）:\n" % len(courses))
        for i, c in enumerate(courses):
            mark = "★" if i in sug else " "
            avail = c.get("available") or "-"
            la = (c.get("lastAccessed") or "")[:10]
            print("   %s %3d) [%-4s] %s  %s" % (mark, i + 1, avail, la, c["name"][:66]))

    if not courses:
        picked = []
    elif args.courses:
        picked = parse_sel("all" if args.courses == "all" else args.courses, len(courses))
    elif interactive:
        print("\n  输入序号选择（如 1,3,5-7 / all / 回车=自动选★）")
        raw = ask("选择", default="auto")
        picked = sorted(sug) if raw == "auto" else parse_sel(raw, len(courses))
    else:
        picked = sorted(sug)

    if not picked:
        warn("没有选中任何课程。可稍后重跑本向导。")
        sel = []
    else:
        sel = [{"name": courses[i]["name"], "id": courses[i]["id"]} for i in picked]

    if args.list:
        hr("仅列出（--list），未写配置")
        return 0

    cfg["courses"] = sel
    if args.download_dir:
        cfg["download_dir"] = args.download_dir
    if args.state_dir:
        cfg["state_dir"] = args.state_dir
    p = bb_core.save_config(cfg, args.config)
    ok("配置已写入：%s" % p)
    ok("已选 %d 门课进入守望" % len(sel))
    for c in sel:
        print("     · %s" % c["name"][:70])

    for f in ("_setup_cookies.txt", "cookies.txt", "cookies_aria.txt"):
        fp = os.path.join(bb_core.HOME_DIR, f)
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
    for d in ("tmp",):
        dp = os.path.join(bb_core.HOME_DIR, d)
        if os.path.isdir(dp):
            import shutil
            shutil.rmtree(dp, ignore_errors=True)

    hr("完成")
    print("  下一步：python bb_watch.py --baseline     # 建立首次快照")
    print("          python bb_watch.py                # 之后每轮只在有新增时输出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
