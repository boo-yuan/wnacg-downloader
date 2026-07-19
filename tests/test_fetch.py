import asyncio
import httpx
from bs4 import BeautifulSoup
import traceback

async def main():
    async with httpx.AsyncClient(verify=False) as client:
        # 1. Search page
        search_url = "https://www.wnacg.com/search/index.php?q=%E7%BE%8E%E4%B8%BD%E6%96%B0%E4%B8%96%E7%95%8C&m=&syn=yes&f=_all&s=create_time_DESC&p=1"
        try:
            r = await client.get(search_url, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            items = soup.select('.gallary_item')
            print(f"Found {len(items)} items in search.")
            if items:
                first = items[0]
                title = first.select_one('.title a').text.strip()
                link = first.select_one('.title a')['href']
                print(f"First item: {title}, link: {link}")
        except Exception as e:
            print("Error fetching search:")
            traceback.print_exc()

        # 2. Index page
        index_url = "https://www.wnacg.com/photos-index-page-1-aid-230296.html"
        try:
            r = await client.get(index_url, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            pics = soup.select('.pic_box a')
            print(f"Found {len(pics)} pictures in index.")
            if pics:
                first_pic = pics[0]['href']
                print(f"First picture link: {first_pic}")
        except Exception as e:
            print("Error fetching index:")
            traceback.print_exc()

        # 3. View page
        view_url = "https://www.wnacg.com/photos-view-id-19430421.html"
        try:
            r = await client.get(view_url, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            img = soup.select_one('#picarea')
            if img:
                print(f"Image original url: {img.get('src', '')}")
            else:
                print("No #picarea found.")
        except Exception as e:
            print("Error fetching view:")
            traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())
