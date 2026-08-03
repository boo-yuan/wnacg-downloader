import asyncio
from curl_cffi import requests
from bs4 import BeautifulSoup
import re
import json

async def main():
    s = requests.AsyncSession(impersonate='chrome', verify=False)
    r = await s.get('https://www.wnacg.ru/')
    soup = BeautifulSoup(r.text, 'html.parser')
    a = soup.select_one('.pic_box a')
    aid = a['href'].split('-')[-1].split('.')[0]
    
    url = f'https://www.wnacg.ru/photos-gallery-aid-{aid}.html'
    r2 = await s.get(url)
    
    matches = re.search(r'var\s+imglist\s*=\s*(\[.*?\]);', r2.text, re.DOTALL)
    if matches:
        print(matches.group(1)[:500])
        urls = re.findall(r'url\s*:\s*(?:fast_img_host\+)?\"(.*?)\"', matches.group(1))
        print(f'Found {len(urls)} urls!')
        for i in range(min(5, len(urls))):
            u = urls[i]
            if u.startswith('//'): u = 'https:' + u
            print(u)
        
asyncio.run(main())
