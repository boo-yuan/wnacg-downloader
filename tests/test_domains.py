import asyncio
from curl_cffi.requests import AsyncSession

async def main():
    url = f"https://www.wnacg.vip/search/index.php?q=a&m=&syn=yes&f=_all&s=create_time_DESC&p=1"
    try:
        async with AsyncSession(impersonate="chrome120", timeout=10) as s:
            r = await s.get(url)
            print(f"Status: {r.status_code}")
            print(r.text[:500])
            print("...")
            print(r.text[-500:])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
