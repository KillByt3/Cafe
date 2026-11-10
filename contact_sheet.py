from pathlib import Path
from PIL import Image, ImageDraw
root=Path(r"C:\Users\OSCRPION\Documents\claude\cafe-qr-system")
files=sorted((root/"static"/"products").glob("[0-9]*.jpg"),key=lambda p:int(p.stem))
print("numeric product files",len(files))
print("unique files",len({p.read_bytes() for p in files}))
w,h=120,112
sheet=Image.new("RGB",(w*8,h*((len(files)+7)//8)),"#eeeeee")
d=ImageDraw.Draw(sheet)
for i,p in enumerate(files):
 im=Image.open(p).convert("RGB")
 im.thumbnail((110,85))
 x=(i%8)*w+(w-im.width)//2
 y=(i//8)*h
 sheet.paste(im,(x,y))
 d.text(((i%8)*w+6,y+87),p.stem,fill="#222")
sheet.save(root/"work"/"products_contact.jpg",quality=85)
