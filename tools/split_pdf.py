#!/usr/bin/env python3
"""把大 PDF 按体积切成小分卷，使每个文件都低于 GitHub 的单文件 100MB 限制。

不重新编码页面内容（只是搬运页面对象），所以画质与原文件一致。

用法:
    python tools/split_pdf.py 源.pdf 输出目录 [--max-mb 38] [--prefix "数学-7星学霸"]

输出命名:
    <prefix>01_第001-014页.pdf
    <prefix>02_第015-028页.pdf
"""
import argparse
import os
import sys

import pymupdf

SAFETY = 0.9  # 目标体积打九折，给 PDF 结构开销留余量


def save_range(doc, start, end, path):
    """保存 [start, end) 的页面（0 基、左闭右开）为新 PDF。"""
    out = pymupdf.open()
    out.insert_pdf(doc, from_page=start, to_page=end - 1)
    out.save(path, garbage=4, deflate=True)
    out.close()
    return os.path.getsize(path)


def split(doc, start, end, prefix, outdir, max_bytes, files):
    """递归切分区间，保证每卷 <= max_bytes。"""
    n = end - start
    if n <= 0:
        return
    # 先用临时名，最后统一按顺序重编号
    path = os.path.join(outdir, f"_tmp_{start:04d}_{end:04d}.pdf")
    size = save_range(doc, start, end, path)

    if size <= max_bytes or n == 1:
        files.append((len(files) + 1, start + 1, end, path, size))
        return

    os.remove(path)
    mid = start + n // 2
    split(doc, start, mid, prefix, outdir, max_bytes, files)
    split(doc, mid, end, prefix, outdir, max_bytes, files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--max-mb", type=float, default=38.0)
    ap.add_argument("--prefix", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    doc = pymupdf.open(a.src)
    total = doc.page_count
    size = os.path.getsize(a.src)
    max_bytes = int(a.max_mb * 1048576)

    os.makedirs(a.out, exist_ok=True)
    # 先按均值估算页数，再靠递归校验兜底
    per_page = max(size / total, 1)
    step = max(1, int(max_bytes * SAFETY / per_page))

    files = []
    for s in range(0, total, step):
        split(doc, s, min(s + step, total), a.prefix, a.out, max_bytes, files)

    # 重新按顺序编号，避免递归产生的序号空洞
    for i, (_, s, e, path, sz) in enumerate(files, 1):
        new = os.path.join(os.path.dirname(path), f"{a.prefix}{i:02d}_第{s:03d}-{e:03d}页.pdf")
        if new != path:
            os.replace(path, new)
        files[i - 1] = (i, s, e, new, sz)

    doc.close()

    ok_pages = sum(e - s + 1 for _, s, e, _, _ in files)
    assert ok_pages == total, f"页数不匹配: {ok_pages} != {total}"

    if not a.quiet:
        print(f"{os.path.basename(a.src)}: {total} 页 / {size/1048576:.0f}MB "
              f"-> {len(files)} 卷, 最大 {max(f[4] for f in files)/1048576:.1f}MB")
    return files


if __name__ == "__main__":
    main()
