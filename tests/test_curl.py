import asyncio
from curl_cffi.requests import AsyncSession

async def main():
    async with AsyncSession(impersonate="chrome") as s:
        r = await s.get("https://www.wnacg.com/search/index.php?q=%E7%BE%8E%E4%B8%BD%E6%96%B0%E4%B8%96%E7%95%8C")
        print(r.status_code)
        print(r.text[:200])

asyncio.run(main())
