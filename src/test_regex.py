import re
text = r'[{ url: fast_img_host+"//img5.qy0.ru/data/3747/47/0001.webp", caption: "[0001]"}]'
print(re.findall(r'url:\s*(?:fast_img_host\+)?\"(.*?)\"', text))
