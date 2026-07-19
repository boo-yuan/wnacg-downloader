import asyncio
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

async def main():
    try:
        async with AsyncSession(impersonate="chrome120", timeout=10) as s:
            r = await s.get("https://wnacg01.link/")
            print(f"Status: {r.status_code}")
            soup = BeautifulSoup(r.text, 'html.parser')
            print("Links found:")
            for a in soup.find_all('a'):
                text = a.text.replace('\xa0', ' ').strip()
                href = a.get('href', '')
                print(f"HREF: {href} | TEXT: {text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
