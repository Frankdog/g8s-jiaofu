#!/bin/bash
# 分本逐次推送：每次只推一本书（约 200MB），避免代理在 400MB 处断连导致全盘重来。
set -uo pipefail
cd "$HOME/Documents/8上教辅" || exit 1

AUTH=$(gh auth token)
B64=$(printf "x-access-token:%s" "$AUTH" | base64)
g() { git -c "http.extraheader=AUTHORIZATION: basic $B64" \
          -c http.postBuffer=524288000 -c http.version=HTTP/1.1 "$@"; }

NAME=$(git config --global --get user.name); NAME=${NAME:-Frankdog}
MAIL=$(git config --global --get user.email); MAIL=${MAIL:-frankdog@users.noreply.github.com}

rm -rf .git
git init -q -b main
git config user.name "$NAME"
git config user.email "$MAIL"

echo "== 1/7 说明与脚本 =="
git add .gitignore tools README.md 清单.csv
git commit -qm "初始化：README、清单与切分脚本"
g push -q -u origin main && echo "   ✓ 已推送" || { echo "   ✗ 失败"; exit 1; }

i=2
for d in "物理/4星学霸题中题（2027版）" \
         "物理/4星学霸题中题（2026秋版）" \
         "数学/5星学霸·几何模型" ; do
  echo "== $i/7 $d =="
  git add "$d"
  git commit -qm "新增：$d"
  if g push -q origin main; then
    echo "   ✓ 已推送（$(du -sh "$d" | cut -f1)）"
  else
    echo "   ✗ 推送失败，重试一次…"
    sleep 3
    g push -q origin main && echo "   ✓ 重试成功" || echo "   ✗ 仍失败"
  fi
  i=$((i+1))
done

echo "== 完成 =="
gh repo view Frankdog/g8s-jiaofu --json diskUsage,defaultBranchRef 2>&1 | head -2
