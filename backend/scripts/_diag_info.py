"""诊断信息编译阶段卡死：逐个文件处理，每文件加超时，定位挂起文件。"""
import asyncio
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services.compiler.information_compiler import InformationCompiler

ENTERPRISE_ID = "9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e"
FOLDER = os.path.join(settings.SAMPLE_DATA_DIR, "example-enterprise")


async def main():
    c = InformationCompiler()
    scanned = c._scan_folder(FOLDER, c._get_allowed_root(FOLDER))
    print(f"扫描到 {len(scanned)} 个文件")
    for i, sf in enumerate(scanned):
        t0 = time.time()
        try:
            content = await asyncio.wait_for(c._parse_file(sf.path, c._get_allowed_root(FOLDER)), timeout=30)
            print(f"[{i+1}/{len(scanned)}] parse OK ({time.time()-t0:.1f}s) len={len(content)} {os.path.basename(sf.path)}")
        except asyncio.TimeoutError:
            print(f"[{i+1}/{len(scanned)}] PARSE TIMEOUT (>30s): {os.path.basename(sf.path)}")
            continue
        except Exception as e:
            print(f"[{i+1}/{len(scanned)}] parse FAIL: {os.path.basename(sf.path)}: {type(e).__name__} {str(e)[:80]}")
            continue
        if not content or len(content.strip()) < 10:
            print(f"[{i+1}/{len(scanned)}] skip empty: {os.path.basename(sf.path)}")
            continue
        t1 = time.time()
        try:
            entries = await asyncio.wait_for(c._extract_entities(None, content, sf), timeout=60)
            print(f"[{i+1}/{len(scanned)}] NER OK ({time.time()-t1:.1f}s) entries={len(entries)} {os.path.basename(sf.path)}")
        except asyncio.TimeoutError:
            print(f"[{i+1}/{len(scanned)}] NER TIMEOUT (>60s): {os.path.basename(sf.path)}")
            continue
        except Exception as e:
            print(f"[{i+1}/{len(scanned)}] NER FAIL: {os.path.basename(sf.path)}: {type(e).__name__} {str(e)[:80]}")
            continue


if __name__ == "__main__":
    asyncio.run(main())