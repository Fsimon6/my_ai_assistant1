import traceback
from backend.main import app
import httpx

transport = httpx.ASGITransport(app=app)
try:
    async def main():
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
            try:
                r = await c.post('/api/v1/auth/register', json={'email':'a@b.com','username':'u1','password':'p123456','full_name':'U1'})
                print('REG', r.status_code, r.text[:300])
            except Exception:
                traceback.print_exc()
    import asyncio
    asyncio.run(main())
except Exception:
    traceback.print_exc()
