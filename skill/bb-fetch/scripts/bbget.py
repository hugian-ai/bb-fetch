#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bbget.py — Blackboard 高速下载器（aria2 通道）

用法：
  python bbget.py <BB链接或 /bbcswebdav/... 相对路径> [--dir 目录] [--out 文件名] [--conns 16]

流程：复用/刷新 BB 会话 → 生成 aria2 可读 cookie（自动剥离 #HttpOnly_ 前缀）→
      aria2 -x16 -s16 多线程下载（断点续传 + 自动重试）。

坑位备忘：
  · BB 会话 cookie 是 HttpOnly，curl 写的 jar 带 #HttpOnly_ 前缀，aria2 会当注释跳过 → 本脚本自动处理。
  · Git Bash 里传 `/bbcswebdav/...` 会被 MSYS 当本地路径转换 → 先 `export MSYS_NO_PATHCONV=1`。
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bb_core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Blackboard 高速下载（aria2）")
    ap.add_argument("url", help="BB 文件链接（完整 URL 或 /bbcswebdav/... 开头）")
    ap.add_argument("--dir", default=None, help="目标目录（默认取配置的 download_dir）")
    ap.add_argument("--out", default=None, help="保存文件名（默认服务器真名）")
    ap.add_argument("--conns", type=int, default=16, help="并发连接数（默认 16）")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = bb_core.load_config(args.config)
    user, pw, base = bb_core.get_creds(cfg)
    if not (user and pw and base):
        print("未配置：请先运行 bb_setup.py")
        return 2

    bb = bb_core.BB(base, os.path.join(cfg["state_dir"], "cookies.txt"))
    try:
        bb.ensure_login(user, pw)
    except Exception as e:  # noqa: BLE001
        print("登录失败：%s" % str(e)[:160])
        return 3
    cookief = bb.aria2_cookie_file()

    url = args.url
    if url.startswith("/"):
        url = base + url
    if not url.lower().startswith("http"):
        print("非法链接:", url)
        return 2

    aria2c = cfg.get("aria2c") or "aria2c"
    if os.sep in aria2c and not os.path.exists(aria2c):
        print("找不到 aria2c:", aria2c, "\n  请在配置里改 aria2c 路径，或把它加入 PATH")
        return 2

    target_dir = args.dir or cfg.get("download_dir")
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    cmd = [aria2c,
           "-x%d" % args.conns, "-s%d" % args.conns, "-k1M", "-j1", "-c",
           "--load-cookies", cookief,
           "-U", bb_core.UA,
           "--referer", base + "/",
           "--max-tries=100", "--retry-wait=2", "--connect-timeout=15", "--timeout=30",
           "--summary-interval=10", "--console-log-level=warn",
           "--file-allocation=none", "--auto-file-renaming=false", "--allow-overwrite=true"]
    if target_dir:
        cmd += ["--dir", target_dir]
    if args.out:
        cmd += ["--out", args.out]
    cmd.append(url)

    print("bbget →", url)
    if target_dir:
        print("目标目录:", target_dir)
    rc = subprocess.run(cmd).returncode
    print("aria2 exit:", rc)

    for f in ("cookies.txt", "cookies_aria.txt"):
        p = os.path.join(cfg["state_dir"], f)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
