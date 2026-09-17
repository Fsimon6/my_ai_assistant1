# -*- coding: utf-8 -*-
import os, asyncio, shutil
os.environ['VECTOR_DB_PATH'] = './data/chroma_lifecycle'
os.environ['DATABASE_URL'] = 'sqlite:///./data/lifecycle.db'
ROOT = r'C:\Users\Administrator\Desktop\my_ai_assistant'
os.chdir(ROOT)
shutil.rmtree('./data/chroma_lifecycle', ignore_errors=True)
shutil.rmtree('./data/lifecycle.db', ignore_errors=True)
from backend.services.rag_service import get_rag_service
from backend.services.vector_service import get_vector_store_manager

async def main():
    rs = get_rag_service()
    open('./data/_a.txt','w',encoding='utf-8').write('机密代号 MARK-A29ZZZ-XYZ\n机密内容 TOKEN-A29ZZZ\n')
    rA = await rs.process_and_store_document('./data/_a.txt', {}, user_id=1)
    da = rA.get('document_id'); print('docA=', da)
    open('./data/_b.txt','w',encoding='utf-8').write('机密代号 MARK-B29YYY-XYZ\n机密内容 TOKEN-B29YYY\n')
    rB = await rs.process_and_store_document('./data/_b.txt', {}, user_id=2)
    db = rB.get('document_id'); print('docB=', db)

    out=[]
    async for c in rs.rag_query('TOKEN-A29ZZZ', user_id=1): out.append(c)
    ra = ''.join(out)
    print('A(user1) query A-secret -> contains marker?', 'MARK-A29ZZZ-XYZ' in ra)
    print('   response[:160]:', ra[:160])

    vs = get_vector_store_manager()
    cnt = await vs.delete_documents([da], user_id=1)
    print('delete A deleted_count=', cnt)

    out2=[]
    async for c in rs.rag_query('TOKEN-A29ZZZ', user_id=1): out2.append(c)
    print('A(user1) re-query after delete -> contains marker?', 'MARK-A29ZZZ-XYZ' in ''.join(out2))

    # 越权：user2 删 user1 的 doc
    cntx = await vs.delete_documents([da], user_id=2)
    print('cross-delete (user2 deletes docA) deleted_count=', cntx)

asyncio.run(main())
shutil.rmtree('./data/chroma_lifecycle', ignore_errors=True)
shutil.rmtree('./data/lifecycle.db', ignore_errors=True)
