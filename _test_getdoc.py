import sys, asyncio
sys.path.insert(0, r"C:\Users\Administrator\Desktop\my_ai_assistant")
from backend.services.vector_service import get_vector_store_manager

vs = get_vector_store_manager()

async def main():
    r = await vs.get_document_chunks("6d62e2010b5a416fb71e193b4a8e57c6", user_id=85)
    print("6d62 + user_id=85 -> chunks:", len(r))
    for c in r[:1]:
        print("   first:", c["index"], repr(c["content"][:40]), "src=", c["source"])

    r2 = await vs.get_document_chunks("6d62e2010b5a416fb71e193b4a8e57c6")
    print("6d62 no user_id   -> chunks:", len(r2))

    r3 = await vs.get_document_chunks("8df317b3f09144ba9a52c0ed139cf221", user_id=85)
    print("8df3 + user_id=85 -> chunks:", len(r3))

asyncio.run(main())
