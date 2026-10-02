import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.compiler.information_compiler import InformationCompiler

async def main():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "sample_data", "example-enterprise", "01-company", "org-structure.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    comp = InformationCompiler()
    entries = comp._rule_based_org_extraction(content, path, "markdown")
    print(f"Total entries: {len(entries)}")
    from collections import Counter
    types = Counter(e.entry_type for e in entries)
    print("Types:", dict(types))
    for e in entries:
        print(f"  [{e.entry_type}] {e.name} | attrs={e.attributes}")

asyncio.run(main())