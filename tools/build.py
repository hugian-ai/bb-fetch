#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py — 校验并打包 bb-fetch 技能包。

用法：
  python tools/build.py            # 校验 + 打包到 dist/bb-fetch.zip
  python tools/build.py --check    # 只校验，不打包

校验项：
  · SKILL.md 存在，frontmatter 含 name / description（必填）
  · name 与技能文件夹名一致
  · scripts/ 下 Python 文件语法可编译
  · 技能包内**不含**疑似凭据/私密文件（config.json 里的真实密码、cookies、.env 等）
"""
import argparse
import os
import py_compile
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skill", "bb-fetch")
DIST = os.path.join(ROOT, "dist")

EXCLUDE_DIRS = {"__pycache__", ".git"}
EXCLUDE_FILES = {"cookies.txt", "cookies_aria.txt", "_setup_cookies.txt", ".env"}
SECRET_HINT = re.compile(r"(password|passwd|secret|token)\s*[:=]\s*[\"'][^\"']{6,}[\"']", re.I)


def fail(msg):
    print("  [x] " + msg)
    return False


def ok(msg):
    print("  [v] " + msg)
    return True


def parse_frontmatter(text):
    """解析 YAML frontmatter，支持 `key: |` 多行块标量。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None
    fm = {}
    key = None
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if mm:
            key = mm.group(1)
            val = mm.group(2).strip()
            if val in ("|", ">", "|-", ">-"):
                fm[key] = ""          # 块标量：内容在后续缩进行
            else:
                fm[key] = val
        elif key and line.strip() and line.startswith((" ", "\t")):
            fm[key] = (fm.get(key, "") + " " + line.strip()).strip()
    return fm


def validate():
    good = True
    skill_md = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.exists(skill_md):
        return fail("缺少 SKILL.md：%s" % skill_md)
    ok("SKILL.md 存在")

    with open(skill_md, encoding="utf-8") as f:
        fm = parse_frontmatter(f.read())
    if not fm:
        return fail("SKILL.md 缺少 YAML frontmatter")
    if not fm.get("name"):
        good = fail("frontmatter 缺 name")
    elif fm["name"] != os.path.basename(SKILL_DIR):
        good = fail("name(%s) 与文件夹名(%s) 不一致" % (fm["name"], os.path.basename(SKILL_DIR)))
    else:
        ok("name = %s" % fm["name"])
    if not fm.get("description"):
        good = fail("frontmatter 缺 description")
    else:
        ok("description 存在（%d 字）" % len(fm["description"]))
    if fm.get("agent_created", "").lower() != "true":
        print("  [!] 建议在 frontmatter 加 agent_created: true")

    # 语法检查
    scripts = os.path.join(SKILL_DIR, "scripts")
    for fn in sorted(os.listdir(scripts)):
        if fn.endswith(".py"):
            try:
                py_compile.compile(os.path.join(scripts, fn), doraise=True)
                ok("语法 OK  scripts/%s" % fn)
            except py_compile.PyCompileError as e:
                good = fail("语法错误 %s: %s" % (fn, str(e)[:120]))

    # 敏感信息扫描
    for base, dirs, files in os.walk(SKILL_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in files:
            p = os.path.join(base, fn)
            rel = os.path.relpath(p, SKILL_DIR)
            if fn in EXCLUDE_FILES:
                good = fail("技能包内出现敏感文件名：%s" % rel)
            if fn.endswith((".py", ".md", ".json")):
                try:
                    txt = open(p, encoding="utf-8").read()
                except Exception:
                    continue
                if SECRET_HINT.search(txt):
                    good = fail("疑似硬编码凭据：%s" % rel)
    if good:
        ok("未发现疑似凭据/私密文件")
    return good


def build():
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "bb-fetch.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(SKILL_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in files:
                if fn in EXCLUDE_FILES or fn.endswith(".pyc"):
                    continue
                p = os.path.join(base, fn)
                arc = os.path.join("bb-fetch", os.path.relpath(p, SKILL_DIR))
                z.write(p, arc)
                n += 1
    print("  [v] 已打包 %d 个文件 → %s" % (n, out))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不打包")
    a = ap.parse_args()
    print("== 校验 bb-fetch ==")
    if not validate():
        print("\n校验未通过，已中止。")
        sys.exit(1)
    if not a.check:
        print("\n== 打包 ==")
        build()
    print("\n完成。")
