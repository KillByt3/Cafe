import json, requests, pathlib
root = pathlib.Path(r"C:\Users\OSCRPION\Documents\claude\cafe-qr-system")
data = json.loads((root/"work"/"pixabay_images.json").read_text(encoding="utf-8-sig"))
out = root/"static"/"products"
ok, errors = [], []
for i,item in data.items():
    dest=out/f"{i}.jpg"
    if dest.exists() and dest.stat().st_size>10000: continue
    try:
        r=requests.get(item["url"],timeout=20)
        r.raise_for_status()
        if not r.headers.get("content-type","").startswith("image/"): raise ValueError("not an image")
        dest.write_bytes(r.content); ok.append(i)
    except Exception as e: errors.append((i,str(e)))
print("downloaded",len(ok),"additional files")
print("errors",errors)
