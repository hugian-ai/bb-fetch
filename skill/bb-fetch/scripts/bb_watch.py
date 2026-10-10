# -*- coding: utf-8 -*-
"""bb_watch.py — Blackboard 新内容守望者

每轮：登录 → 读取要盯的课程 → BFS 抓内容树 + 公告 → 与上次快照比对。
纪律：无变化 / 抓取不全 / 出错 → 一律静默（stdout 空，不推送）。
      只有真新增时才向 stdout 输出提醒文本（由调用方捕获并推送）。

用法：
  python bb_watch.py                # 常规一轮
  python bb_watch.py --baseline     # 重建基线（不报警）
  python bb_watch.py --status       # 打印当前快照概况，不联网
  python bb_watch.py --verbose      # 过程信息到 stderr（stdout 仍只放告警）
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bb_core  # noqa: E402

LOCK_STALE_S = 1200  # 20 分钟


def log(state_dir, msg):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "bb_watch.log"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def acquire_lock(state_dir, verbose):
    os.makedirs(state_dir, exist_ok=True)
    lock = os.path.join(state_dir, ".lock")
    if os.path.exists(lock):
        if time.time() - os.path.getmtime(lock) < LOCK_STALE_S:
            if verbose:
                sys.stderr.write("[bb_watch] skip: another run in progress\n")
            sys.exit(0)
        shutil.rmtree(lock, ignore_errors=True)
    try:
        os.mkdir(lock)
    except FileExistsError:
        sys.exit(0)
    return lock


def fmt_alert(new_f, new_a, files, anns):
    lines = ["**Blackboard 更新** " + time.strftime("%m-%d %H:%M")]
    if new_f:
        by = {}
        for u in new_f:
            by.setdefault(files[u]["c"], []).append((files[u]["t"], u))
        for c, items in by.items():
            lines.append("- **%s** 新增文件 %d：" % (c, len(items)))
            for t, u in items:
                lines.append("  - `%s`" % t)
                lines.append("    %s" % u)
    if new_a:
        by = {}
        for k in new_a:
            by.setdefault(anns[k]["c"], []).append((anns[k]["t"], k))
        for c, items in by.items():
            lines.append("- **%s** 新公告 %d：" % (c, len(items)))
            for t, _u in items:
                lines.append("  - %s" % t)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Blackboard 新内容守望者")
    ap.add_argument("--config")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg = bb_core.load_config(args.config)
    state_dir = cfg["state_dir"]
    state_file = os.path.join(state_dir, "state.json")

    if args.status:
        if not os.path.exists(state_file):
            print("(尚无快照)")
            return 0
        st = json.load(open(state_file, encoding="utf-8"))
        print("快照时间: %s" % st.get("updated"))
        print("文件条目: %d  公告: %d" % (len(st.get("files", {})), len(st.get("anns", {}))))
        by = {}
        for v in st.get("files", {}).values():
            by[v["c"]] = by.get(v["c"], 0) + 1
        for c, n in sorted(by.items(), key=lambda x: -x[1]):
            print("  %-40s %d" % (c[:40], n))
        return 0

    lock = acquire_lock(state_dir, args.verbose)
    try:
        user, pw, base = bb_core.get_creds(cfg)
        if not (user and pw and base):
            log(state_dir, "error: 未配置（缺站点或凭据），请先运行 bb_setup.py")
            if args.verbose:
                sys.stderr.write("[bb_watch] 未配置：请先运行 bb_setup.py\n")
            return 0

        bb = bb_core.BB(base, os.path.join(state_dir, "cookies.txt"))
        bb.login(user, pw)
        if args.verbose:
            sys.stderr.write("[bb_watch] 登录成功 @ %s\n" % base)

        courses = bb_core.courses_from_config(cfg) or bb_core.discover_courses(bb, cfg)
        if not courses:
            log(state_dir, "error: 没有要盯的课程，请运行 bb_setup.py --refresh")
            return 0
        if args.verbose:
            sys.stderr.write("[bb_watch] 课程 %d 门\n" % len(courses))

        files, anns, failed, empty = bb_core.crawl_all(bb, courses)
        if empty and args.verbose:
            sys.stderr.write("[bb_watch] 空课(忽略): %s\n" % " | ".join(empty))
        if failed:
            log(state_dir, "incomplete scan, skip compare: " + " | ".join(failed))
            if args.verbose:
                sys.stderr.write("[bb_watch] 抓取不全，本轮作废: %s\n" % " | ".join(failed))
            return 0

        new_state = {"files": files, "anns": anns, "courses": courses,
                     "updated": time.strftime("%Y-%m-%d %H:%M:%S")}

        if args.baseline or not os.path.exists(state_file):
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(new_state, f, ensure_ascii=False, indent=1)
            log(state_dir, "baseline: %d files, %d anns" % (len(files), len(anns)))
            return 0

        old = json.load(open(state_file, encoding="utf-8"))
        new_f = set(files) - set(old.get("files", {}))
        new_a = set(anns) - set(old.get("anns", {}))

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(new_state, f, ensure_ascii=False, indent=1)

        if not new_f and not new_a:
            log(state_dir, "no change (%d files, %d anns)" % (len(files), len(anns)))
            return 0

        log(state_dir, "ALERT: +%d files, +%d anns" % (len(new_f), len(new_a)))
        print(fmt_alert(new_f, new_a, files, anns))
        return 0
    except Exception as e:  # noqa: BLE001
        log(cfg["state_dir"], "error: " + str(e)[:200])
        if args.verbose:
            sys.stderr.write("[bb_watch] ERROR: %s\n" % str(e)[:200])
        return 0
    finally:
        shutil.rmtree(os.path.join(cfg["state_dir"], ".lock"), ignore_errors=True)
        jar = os.path.join(cfg["state_dir"], "cookies.txt")
        if os.path.exists(jar):
            try:
                os.remove(jar)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main() or 0)
