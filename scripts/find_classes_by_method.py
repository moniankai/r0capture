"""用 androguard 扫 DEX,找出包含目标方法名的类全名."""
from __future__ import annotations
import sys
from pathlib import Path

# suppress androguard debug logs
from loguru import logger as _ag_logger
_ag_logger.remove()
_ag_logger.add(sys.stderr, level="WARNING")

from androguard.core.dex import DEX

EXTRACTED = Path("d:/tmp/phoenix_apk/extracted")
TARGET_METHODS = {
    "playItemOfN", "doSetPlayItem", "setPlayEpisode", "getPlayEpisode",
    "setCurrentPlayIndex", "playNext", "playPrev", "playPrevious",
    "playPrevChapter", "replayItem", "doPlayItemOfN",
    "onEpisodeClick", "selectEpisode", "setEpisode",
}


def main():
    dexes = sorted(EXTRACTED.glob("*.dex"))
    print(f"Scanning {len(dexes)} DEX files...", flush=True)

    hits: dict[str, list[tuple[str, str, str]]] = {}
    for idx, dex_path in enumerate(dexes):
        dex = DEX(dex_path.read_bytes())
        cls_count = 0
        for cls in dex.get_classes():
            cls_count += 1
            cls_name = cls.get_name()  # 'Lcom/xxx/Yyy;'
            if not (cls_name.startswith("Lcom/phoenix") or
                    cls_name.startswith("Lcom/bytedance") or
                    cls_name.startswith("Lcom/ss/") or
                    cls_name.startswith("Lcom/dragon/")):
                continue
            for m in cls.get_methods():
                mname = m.get_name()
                if mname in TARGET_METHODS:
                    sig = m.get_descriptor()
                    hits.setdefault(mname, []).append((dex_path.name, cls_name, sig))
        print(f"  [{idx+1}/{len(dexes)}] {dex_path.name}: {cls_count} classes", flush=True)

    print("\n====== HITS ======")
    for mname in sorted(hits.keys()):
        items = hits[mname]
        print(f"\n## {mname}  ({len(items)})")
        seen = set()
        for dex, cls, sig in items:
            key = (cls, sig)
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{dex}] {cls}{sig}")

    out = Path("d:/tmp/static_hits.txt")
    with out.open("w", encoding="utf-8") as f:
        for mname in sorted(hits.keys()):
            items = hits[mname]
            f.write(f"## {mname}  ({len(items)})\n")
            seen = set()
            for dex, cls, sig in items:
                if (cls, sig) in seen:
                    continue
                seen.add((cls, sig))
                f.write(f"  [{dex}] {cls}{sig}\n")
    print(f"\n写入 {out}")


if __name__ == "__main__":
    main()
