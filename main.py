import re
import time
import argparse
import httpx
import pikepdf
from pathlib import Path
from PIL import Image

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
           " AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/74.0.3729.169 Safari/537.36"}

COLORSPACE_MAP = {
    'RGB':  pikepdf.Name.DeviceRGB,
    'CMYK': pikepdf.Name.DeviceCMYK,
    'L':    pikepdf.Name.DeviceGray,
}


def new_name(title):
    # '/ \ : * ? " < > |'
    rstr = r"[\/\\\:\*\?\"\<\>\|\%\=\@\!\@\#\$\%\%\^\&\*\(\)\+\|\`\~]"
    new_doc_name = re.sub(rstr, "_", title)  # 替换为下划线
    return new_doc_name


def img2pdf(img_path, name, num, output):
    pdf = pikepdf.Pdf.new()
    for i in range(1, num):
        num_str = str(i).rjust(3, '0')
        img_file = img_path / f"{name}-{num_str}.jpg"

        with Image.open(img_file) as img:
            w, h = img.size
            colorspace = COLORSPACE_MAP.get(img.mode, pikepdf.Name.DeviceRGB)

        jpeg_data = img_file.read_bytes()

        image_xobj = pikepdf.Stream(pdf, jpeg_data)
        image_xobj.stream_dict = pikepdf.Dictionary(
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=w,
            Height=h,
            ColorSpace=colorspace,
            BitsPerComponent=8,
            Filter=pikepdf.Name.DCTDecode,
        )

        content = f'q {w} 0 0 {h} 0 0 cm /Im0 Do Q'.encode()
        page = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=pikepdf.Array([0, 0, w, h]),
            Resources=pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(image_xobj))
            ),
            Contents=pdf.make_indirect(pikepdf.Stream(pdf, content)),
        ))
        pdf.pages.append(page)

    pdf.save(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download a Pearson Active Learning book as a PDF.")
    parser.add_argument("url", help="Base image URL without the page suffix (e.g. .../images/9781292244778)")
    parser.add_argument("--output", "-o", help="Output PDF path (default: <img_path>/combined.pdf)")
    args = parser.parse_args()

    print('Welcome to use this tool,this tool can help you download pearson active book easily.\n'
          'First,you should get a link which can download any page of book by using developer tool of your browser.\n'
          'Like this :\n'
          'If your link is "https://resources.pearsonactivelearn.com/r00/r0090/r009023/r00902341/current/OPS/images/9781292244778-001.jpg"\n'
          'Then,after deal,you should input link like this "https://resources.pearsonactivelearn.com/r00/r0090/r009023/r00902341/current/OPS/images/9781292244778"\n'
          'Easily understand,isn\'t ?\n'
          'Now  enjoy this tool!\n'
          '(This tool writen by RedSTAR.This tool was open source in Github,link is https://github.com/RedSTARO/Pearson-active-book-downloader .)\n')

    base_url = args.url.rstrip('/')
    img_path = Path("download") / base_url.rsplit('/', 1)[1]
    img_path.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else img_path / f"{img_path.name}.pdf"

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        for i in range(1, 1001):
            num = str(i).rjust(3, '0')
            in_url = base_url + f"-{num}.jpg"

            doc_name = new_name(in_url.rsplit('/', 1)[1])
            if len(doc_name) > 250:
                doc_name = "The file has been renamed,because original file namois too long. Now name:" + Path(doc_name).suffix

            dest = img_path / doc_name
            if dest.exists():
                print(f"Skipping {doc_name} (already downloaded)")
                continue

            print(f"Downloading page {num}: {in_url}")
            try:
                while True:
                    response = client.get(in_url)
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 5))
                        print(f"Rate limited. Retrying in {retry_after}s...")
                        time.sleep(retry_after)
                        continue
                    break
                if response.status_code != 200:
                    print(f"Get {doc_name} failed. Download finished. Packing into pdf...")
                    num_pdf = i
                    break
                dest.write_bytes(response.content)
                print(f"Downloaded {doc_name}")
            except httpx.ConnectError:
                print(f"Download {doc_name} failed! Please confirm your input.")
                continue

    img2pdf(img_path, doc_name.rsplit('-', 1)[0], num_pdf, output)
