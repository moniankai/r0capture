#!/usr/bin/env bash
# 反复跑 download_hongguo.py 直到完成全部集数或达到最大轮数
# 用法: bash scripts/_run_until_done.sh "剧名" 83 20
#
# 每轮：force-stop App → 冷启动 → 等 → 跑 python 下载器（python 脚本内部
# 会做搜索+动态定位剧名+点击进入播放）

DRAMA="$1"
TOTAL="$2"
MAX_ROUNDS="${3:-20}"

if [ -z "$DRAMA" ] || [ -z "$TOTAL" ]; then
    echo "用法: $0 <剧名> <总集数> [最大轮数=20]"
    exit 1
fi

DIR="./videos/$DRAMA"

for i in $(seq 1 "$MAX_ROUNDS"); do
    CURRENT=$(ls "$DIR" 2>/dev/null | grep -c "^episode_")
    echo ""
    echo "========== 轮次 $i/$MAX_ROUNDS | 已下载 $CURRENT/$TOTAL 集 =========="
    if [ "$CURRENT" -ge "$TOTAL" ]; then
        echo "[wrapper] 已达成 $CURRENT/$TOTAL，完成"
        exit 0
    fi

    # 每轮前 force-stop + 冷启动 App
    echo "[wrapper] 重启 App..."
    adb shell am force-stop com.phoenix.read
    sleep 2
    adb shell monkey -p com.phoenix.read -c android.intent.category.LAUNCHER 1 > /dev/null 2>&1
    sleep 10
    adb shell input keyevent KEYCODE_HOME
    sleep 2

    # python 脚本内部会通过 navigate_to_drama_via_search 动态定位并点击剧名
    echo "[wrapper] 启动 python 脚本第 $i 轮..."
    python scripts/download_hongguo.py -n "$DRAMA" --attach-running --total-episodes "$TOTAL" --bootstrap-navigate

    NEW=$(ls "$DIR" 2>/dev/null | grep -c "^episode_")
    echo "[wrapper] 本轮结束 $NEW/$TOTAL"
    if [ "$NEW" -le "$CURRENT" ]; then
        echo "[wrapper] 本轮无进展，等 10 秒后下一轮"
        sleep 10
    fi
done

FINAL=$(ls "$DIR" 2>/dev/null | grep -c "^episode_")
echo ""
echo "========== wrapper 结束 =========="
echo "最终: $FINAL/$TOTAL 集"
