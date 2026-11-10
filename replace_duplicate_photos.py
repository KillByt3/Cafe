import json,requests,pathlib
root=pathlib.Path(r"C:\Users\OSCRPION\Documents\claude\cafe-qr-system")
data=json.loads(r'''{"20":{"url":"https://cdn.pixabay.com/photo/2014/01/15/09/48/iced-coffee-245474_1280.jpg"},"35":{"url":"https://cdn.pixabay.com/photo/2019/07/18/04/23/coffe-4345449_1280.jpg"},"58":{"url":"https://cdn.pixabay.com/photo/2024/01/29/12/56/smoothie-8539791_1280.jpg"}}''')
for i,v in data.items():
 r=requests.get(v["url"],timeout=30);r.raise_for_status()
 (root/"static"/"products"/(i+".jpg")).write_bytes(r.content)
print("replaced three repeated source files")
