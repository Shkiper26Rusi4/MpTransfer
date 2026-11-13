from pathlib import Path
from PIL import Image, ImageDraw

Path("assets").mkdir(exist_ok=True)
im = Image.new("RGBA", (256,256), "#10151f")
d = ImageDraw.Draw(im)
d.rounded_rectangle((12,12,244,244), radius=52, fill="#3369d7")
d.line([(54,89),(194,89)], fill="white", width=17)
d.polygon([(170,60),(209,89),(170,118)], fill="white")
d.line([(202,167),(62,167)], fill="white", width=17)
d.polygon([(86,138),(47,167),(86,196)], fill="white")
im.save("assets/app.ico", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
