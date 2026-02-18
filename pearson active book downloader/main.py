import os
import re
import httpx
import pikepdf
from PIL import Image

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
           " AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/74.0.3729.169 Safari/537.36"}

print('Welcome to use this tool,this tool can help you download pearson active book easily.\n'
      'First,you should get a link which can download any page of book by using developer tool of your browser.\n'
      'Like this :\n'
      'If your link is "https://resources.pearsonactivelearn.com/r00/r0090/r009023/r00902341/current/OPS/images/9781292244778-001.jpg"\n'
      'Then,after deal,you should input link like this "https://resources.pearsonactivelearn.com/r00/r0090/r009023/r00902341/current/OPS/images/9781292244778"\n'
      'Easily understand,isn\'t ?\n'
      'Now  enjoy this tool!\n'
      '(This tool writen by RedSTAR.This tool was open source in Github,link is https://github.com/RedSTARO/Pearson-active-book-downloader .)\n' )

colorspace_map = {
    'RGB':  pikepdf.Name.DeviceRGB,
    'CMYK': pikepdf.Name.DeviceCMYK,
    'L':    pikepdf.Name.DeviceGray,
}


def new_name(title):
    # '/ \ : * ? " < > |'
    rstr = r"[\/\\\:\*\?\"\<\>\|\%\=\@\!\@\#\$\%\%\^\&\*\(\)\-\+\|\`\~]"
    new_doc_name = re.sub(rstr, "_", title)  # 替换为下划线
    return new_doc_name


def get_file_extension(filename):
    arr = os.path.splitext(filename)
    return arr[len(arr) - 1]


def img2pdf(name, num):
    pdf = pikepdf.Pdf.new()
    for i in range(1, num):
        num_str = str(i).rjust(3, '0')
        img_file = img_path + f"{name}_{num_str}.jpg"

        with Image.open(img_file) as img:
            w, h = img.size
            colorspace = colorspace_map.get(img.mode, pikepdf.Name.DeviceRGB)

        with open(img_file, 'rb') as f:
            jpeg_data = f.read()

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

    pdf.save('combined.pdf')


img_path = ".\\download\\"

with httpx.Client(headers=headers, follow_redirects=True) as client:
    for i in range(1,1001):
        num = str(i).rjust(3,'0')
        print(f"Continuous files downloader for pearson active book:task {num}")
        in_url = input("Please input link with out 001.jpg: ") + f"{str(num)}.jpg"
        print(in_url)

        doc_name = in_url.rsplit('/', 1)[1]
        doc_name = new_name(doc_name)
        doc_file = get_file_extension(doc_name)
        if len(doc_name) > 250:
            doc_name = "The file has been renamed,because original file namois too long. Now name:" + str(doc_file)
        try:
            response = client.get(str(in_url))
            r = response.content
            if response.status_code != 200:
                print(f"Get {doc_name} failed.Download finish. Packing into pdf...")
                num_pdf = int(num)
                break
            else:
                with open(".\\download\\" + str(doc_name), "wb") as f:
                    print("Code:" + str(response.status_code))
                    f.write(r)
        except httpx.ConnectError:
            print(f"Download {doc_name} failed！Please confirm your input.")
        if response.status_code == 200:
            print(f"Download {doc_name} success！File is saved in path \"download\". \n")

img2pdf(doc_name.split('_')[0], num_pdf)
