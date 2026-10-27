#!/usr/bin/env python3
"""在 git 协议（github.com:443 / SSH）不可用时，改用 GitHub REST API 把仓库内容写上去。

思路：
  1. 空仓库先用 Contents API 写一个 README，生成首个提交和 main 分支；
  2. 其余文件逐个 POST /git/blobs（base64），拿到 blob sha；
  3. 一次性建 tree（base_tree 指向首提交的 tree）；
  4. 建 commit 并把 refs/heads/main 指过去。

只依赖标准库，token 从 gh 取。

用法:
    python3 tools/push_via_api.py [--limit N] [--dry-run]
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

OWNER, REPO = "Frankdog", "g8s-jiaofu"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _token():
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


TOK = _token()


def call(method, url, payload=None, timeout=600, ok_404=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOK}")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        # 空仓库时 GitHub 对 ref 查询返回 409「Git Repository is empty」
        if ok_404 and (e.code == 404 or (e.code == 409 and "empty" in detail.lower())):
            return None
        raise SystemExit(f"HTTP {e.code} {method} {url}\n{detail}")


def collect():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".git") and d != "__pycache__"]
        for f in sorted(filenames):
            if f.startswith(".") or f.endswith((".log", ".pyc")):
                continue
            p = os.path.join(dirpath, f)
            out.append((os.path.relpath(p, ROOT), p))
    return sorted(out)


def ensure_initial_commit():
    ref = call("GET", f"{API}/git/ref/heads/main", ok_404=True)
    if ref:
        sha = ref["object"]["sha"]
        tree = call("GET", f"{API}/git/commits/{sha}")["tree"]["sha"]
        print(f"仓库已有 main 分支，最新提交 {sha[:8]}")
        return sha, tree

    readme = os.path.join(ROOT, "README.md")
    content = base64.b64encode(open(readme, "rb").read()).decode()
    print("空仓库，先用 README 建立首个提交…")
    res = call("POST", f"{API}/contents/README.md", {
        "message": "初始化：8上教辅分卷库说明",
        "content": content,
        "branch": "main",
    })
    sha = res["commit"]["sha"]
    tree = res["commit"]["tree"]["sha"]
    print(f"  ✓ 首个提交 {sha[:8]}")
    return sha, tree


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只传前 N 个（测速用）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    files = collect()
    if a.limit:
        files = files[:a.limit]
    total = sum(os.path.getsize(p) for _, p in files)
    print(f"待上传 {len(files)} 个文件，共 {total / 1048576:.1f} MB")
    if a.dry_run:
        for rel, p in files:
            print(f"  {os.path.getsize(p) / 1048576:7.1f}MB  {rel}")
        return

    base_commit, base_tree = ensure_initial_commit()

    entries, t0, done = [], time.time(), 0
    for i, (rel, path) in enumerate(files, 1):
        raw = open(path, "rb").read()
        res = call("POST", f"{API}/git/blobs", {
            "content": base64.b64encode(raw).decode(),
            "encoding": "base64",
        })
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": res["sha"]})
        done += len(raw)
        el = time.time() - t0
        speed = done / el / 1048576 if el else 0
        print(f"  [{i}/{len(files)}] {rel}  ({len(raw)/1048576:.1f}MB)  "
              f"累计 {done/1048576:.0f}MB  {speed:.1f}MB/s", flush=True)

    print("建 tree…")
    tree = call("POST", f"{API}/git/trees", {"base_tree": base_tree, "tree": entries})
    print("建 commit…")
    commit = call("POST", f"{API}/git/commits", {
        "message": "8上教辅分卷：物理(2027版/2026秋版) + 数学几何模型\n\n"
                   "物理两本主书按 OCR 目录章节切分，文件名带章名；答案册与几何模型按 38MB 上限切分。\n"
                   "附 split_pdf.py / toc_split.py 与完整清单。",
        "tree": tree["sha"],
        "parents": [base_commit],
    })
    print(f"  ✓ {commit['sha'][:8]}")
    call("PATCH", f"{API}/git/refs/heads/main", {"sha": commit["sha"], "force": True})
    print(f"完成：https://github.com/{OWNER}/{REPO}")


if __name__ == "__main__":
    main()
