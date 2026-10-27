#!/usr/bin/env python3
"""按「目录页」切分扫描版 PDF：OCR 目录 → 还原章节页码 → 按章切开。

这些教辅是扫描件，没有文字层也没有书签，只能靠 OCR 还原目录结构：
  1. 前 16 页找「目录」页；
  2. 目录页用 200/300 两个分辨率各 OCR 一次，交叉比对右栏页码（单次识别常把
     0/0、8/3 之类读成字母，两次比对基本可救回）；
  3. 章标题行本身不带页码，取紧随其后那条条目的页码作为该章起始印刷页；
  4. 用「印刷页 → PDF 页」偏移换算后，逐章 OCR 校验起始页确实有这个章标题，
     对不上就在 ±6 页内搜索，仍找不到则该章起点交由上一章末尾兜底；
  5. 按章切分，单章超过体积上限时对半续拆。

用法:
    python tools/toc_split.py 源.pdf 输出目录 --prefix "主书-" [--max-mb 38] [--dry-run]
"""
import argparse
import os
import re
import sys

import pymupdf
import Vision
from Foundation import NSData

MAX_TOC_SCAN = 16
Y_TOL = 0.012
NUM_X = 0.72
CHAP_RE = re.compile(r"^第([一二三四五六七八九十]+)章")
CN = "一二三四五六七八九十"


# ---------- OCR 基础 ----------

def _ocr(page, dpi):
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(png, len(png)), None)
    handler.performRequests_error_([req], None)
    out = []
    for r in (req.results() or []):
        bb = r.boundingBox()
        out.append((bb.origin.y, bb.origin.x, r.topCandidates_(1)[0].string()))
    return out


def ocr_lines(page, dpi=300):
    """整页按 y 聚成行，返回 [(y, text)]，自上而下。"""
    boxes = sorted(_ocr(page, dpi), key=lambda t: (-t[0], t[1]))
    lines, cur, cur_y = [], [], None
    for y, x, s in boxes:
        if cur_y is None or abs(y - cur_y) <= Y_TOL:
            cur.append((x, s))
            if cur_y is None:
                cur_y = y
        else:
            lines.append((cur_y, "".join(p[1] for p in sorted(cur))))
            cur, cur_y = [(x, s)], y
    if cur:
        lines.append((cur_y, "".join(p[1] for p in sorted(cur))))
    return lines


def ocr_numbers(page):
    """右栏页码：200/300 两档交叉比对，只保留纯数字且唯一的结果。"""
    cand = {}
    for dpi in (200, 300):
        for y, x, s in _ocr(page, dpi):
            if x >= NUM_X:
                cand.setdefault(round(y, 2), set()).add(s.strip())
    out = []
    for y in sorted(cand, reverse=True):
        digits = [v for v in cand[y] if v.isdigit()]
        if len(digits) == 1:
            out.append((y, int(digits[0])))
        elif digits:
            # 多次识别结果不一致时，取最短且最像的（避免把 1 读成 11）
            out.append((y, int(min(digits, key=len))))
    return out


# ---------- 目录解析 ----------

def find_toc_pages(doc):
    hits = []
    for i in range(min(MAX_TOC_SCAN, doc.page_count)):
        txt = "".join(t for _, t in ocr_lines(doc[i]))
        if "目录" in txt or "contents" in txt.lower():
            hits.append(i)
    return hits


def parse_chapters(doc, toc_pages):
    """返回 [(章标题, 印刷页码)]，按目录出现顺序。"""
    entries = []
    for i in toc_pages:
        lines = ocr_lines(doc[i])
        nums = ocr_numbers(doc[i])
        for idx, (y_t, text) in enumerate(lines):
            t = text.strip()
            if not CHAP_RE.match(t):
                continue
            # 章标题所在行通常不带页码；取它下面最近的一个页码
            below = [(y, n) for y, n in nums if y < y_t and y_t - y <= 0.06]
            if not below:
                continue
            entries.append((re.sub(r"^(第.章)\s*", r"\1 ", t), max(below, key=lambda p: p[0])[1]))
    seen, uniq = set(), []
    for t, p in entries:
        if t in seen:
            continue
        seen.add(t)
        uniq.append((t, p))
    return uniq


def chap_index(title):
    m = CHAP_RE.match(title)
    return CN.index(m.group(1)[0]) + 1 if m else 99


def page_text(page):
    return "".join(t for _, t in ocr_lines(page))


def locate(doc, title, hint, lo, hi):
    """校验并定位章首页：先看 hint，偏差则 ±6 页内搜索；找不到返回 None。"""
    key = f"第{title[1]}章"
    if lo <= hint < hi and key in page_text(doc[hint]):
        return hint
    for d in range(1, 7):
        for p in (hint - d, hint + d):
            if lo <= p < hi and key in page_text(doc[p]):
                return p
    return None


def safe_name(s, maxlen=48):
    s = re.sub(r'[\\/:*?"<>|]', "", s)
    return re.sub(r"\s+", "", s)[:maxlen]


def split_range(doc, start, end, outdir, max_bytes, acc):
    tmp = os.path.join(outdir, f"_tmp_{start:04d}_{end:04d}.pdf")
    out = pymupdf.open()
    out.insert_pdf(doc, from_page=start, to_page=end - 1)
    out.save(tmp, garbage=4, deflate=True)
    out.close()
    size = os.path.getsize(tmp)
    if size <= max_bytes or end - start == 1:
        acc.append([start, end, tmp, size])
        return
    os.remove(tmp)
    mid = (start + end) // 2
    split_range(doc, start, mid, outdir, max_bytes, acc)
    split_range(doc, mid, end, outdir, max_bytes, acc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--max-mb", type=float, default=38.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    doc = pymupdf.open(a.src)
    total = doc.page_count
    max_bytes = int(a.max_mb * 1048576)

    toc_pages = find_toc_pages(doc)
    if not toc_pages:
        print(f"NO-TOC {os.path.basename(a.src)} ({total}页)")
        return 1
    chapters = parse_chapters(doc, toc_pages)
    if not chapters:
        print(f"NO-CHAPTERS {os.path.basename(a.src)}")
        return 1

    body_start = max(toc_pages) + 1
    bounds, prev = [], 0
    for i, (title, printed) in enumerate(chapters):
        nxt = [p for _, p in chapters[i + 1:]]
        hi = body_start + (min(nxt) - 1) if nxt else total
        start = locate(doc, title, body_start + printed - 1, prev, max(hi, prev + 1))
        if start is None:
            print(f"    ! {title} 定位失败（预估 PDF 第 {body_start + printed} 页），跳过")
            continue
        bounds.append((title, start))
        prev = start + 1

    print(f"{os.path.basename(a.src)}: {total}页 / 目录{len(toc_pages)}页 / "
          f"{len(bounds)}章 / 正文自PDF第{body_start + 1}页")
    for i, (t, p) in enumerate(bounds):
        e = bounds[i + 1][1] if i + 1 < len(bounds) else total
        print(f"    {t:<20} PDF {p + 1:3d}-{e:3d} ({e - p}页)")
    if a.dry_run:
        return 0

    os.makedirs(a.out, exist_ok=True)
    for f in os.listdir(a.out):
        if f.endswith(".pdf"):
            os.remove(os.path.join(a.out, f))

    for ci, (title, start) in enumerate(bounds, 1):
        end = bounds[ci][1] if ci < len(bounds) else total
        acc = []
        split_range(doc, start, end, a.out, max_bytes, acc)
        multi = len(acc) > 1
        for pi, (s, e, tmp, _) in enumerate(acc, 1):
            tag = f"-{pi:02d}" if multi else ""
            name = f"{a.prefix}{ci:02d}_{safe_name(title)}{tag}_第{s + 1:03d}-{e:03d}页.pdf"
            os.replace(tmp, os.path.join(a.out, name))

    doc.close()
    print(f"    -> {sum(1 for f in os.listdir(a.out) if f.endswith('.pdf'))} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
