import re
import io
import time
import random
import signal
import argparse
import httpx
import pikepdf
from pathlib import Path
from PIL import Image
from tqdm import tqdm

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
           " AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/74.0.3729.169 Safari/537.36"}

COLORSPACE_MAP = {
    'RGB':  pikepdf.Name.DeviceRGB,
    'CMYK': pikepdf.Name.DeviceCMYK,
    'L':    pikepdf.Name.DeviceGray,
}

_current_tmp: Path | None = None


def _sigint_handler(sig, frame):
    if _current_tmp and _current_tmp.exists():
        _current_tmp.unlink()
        tqdm.write(f"Interrupted. Removed incomplete file: {_current_tmp}")
    raise SystemExit(1)


def new_name(title):
    # '/ \ : * ? " < > |'
    rstr = r"[\/\\\:\*\?\"\<\>\|\%\=\@\!\@\#\$\%\%\^\&\*\(\)\+\|\`\~]"
    return re.sub(rstr, "_", title)  # 替换为下划线


def is_valid_jpeg(path):
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except (OSError, SyntaxError):
        return False


def fetch_with_retry(client, url, max_retries, backoff):
    """Fetch url, retrying on 429 and transient errors with exponential backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            response = client.get(url)
        except (httpx.TimeoutException, httpx.ReadError, httpx.ConnectError) as e:
            if attempt == max_retries:
                raise
            wait = backoff * 2 ** (attempt - 1)
            tqdm.write(f"Network error ({e.__class__.__name__}), retrying in {wait}s ({attempt}/{max_retries})...")
            time.sleep(wait)
            continue

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            tqdm.write(f"Rate limited. Retrying in {retry_after}s...")
            time.sleep(retry_after)
            continue

        if response.status_code in (500, 502, 503, 504):
            if attempt == max_retries:
                return response
            wait = backoff * 2 ** (attempt - 1)
            tqdm.write(f"Server error {response.status_code}, retrying in {wait}s ({attempt}/{max_retries})...")
            time.sleep(wait)
            continue

        return response

    return response


def img2pdf(img_path, name, num, output, quiet):
    pdf = pikepdf.Pdf.new()
    for i in tqdm(range(1, num), desc="Building PDF", unit="page", disable=quiet):
        num_str = str(i).rjust(3, '0')
        img_file = img_path / f"{name}-{num_str}.jpg"

        try:
            jpeg_data = img_file.read_bytes()
        except FileNotFoundError:
            tqdm.write(f"Warning: {img_file.name} not found, skipping page {i}.")
            continue

        with Image.open(io.BytesIO(jpeg_data)) as img:
            w, h = img.size
            colorspace = COLORSPACE_MAP.get(img.mode, pikepdf.Name.DeviceRGB)

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
    parser = argparse.ArgumentParser(
        description="Download a Pearson Active Learning book as a PDF.",
        epilog=(
            "Example:\n"
            "  Strip the page suffix from a URL found in your browser's developer tools:\n"
            "  https://resources.pearsonactivelearn.com/.../images/9781292244778-001.jpg\n"
            "  becomes:\n"
            "  %(prog)s https://resources.pearsonactivelearn.com/.../images/9781292244778"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="Base image URL without the page suffix (e.g. .../images/9781292244778)")
    parser.add_argument("--output", "-o", help="Output PDF path (default: <img_path>/<name>.pdf)")
    parser.add_argument("--start", "-s", type=int, default=1, help="Start from this page number (default: 1)")
    parser.add_argument("--pages", "-p", type=int, help="Expected total number of pages (enables ETA in progress bar)")
    parser.add_argument("--delay", "-d", type=float, default=0.5, help="Delay in seconds between requests (default: 0.5)")
    parser.add_argument("--retries", type=int, default=3, help="Max retries on transient errors (default: 3)")
    parser.add_argument("--backoff", type=float, default=2.0, help="Initial backoff in seconds, doubled each retry (default: 2.0)")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF generation after downloading")
    parser.add_argument("--pdf-only", action="store_true", help="Skip downloading and only build PDF from existing images")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress banner and per-page messages")
    args = parser.parse_args()

    base_url = args.url.rstrip('/')
    base_name = base_url.rsplit('/', 1)[1]
    img_path = Path("download") / base_name
    img_path.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else img_path / f"{base_name}.pdf"

    signal.signal(signal.SIGINT, _sigint_handler)

    if args.pdf_only:
        existing = sorted(img_path.glob(f"{base_name}-*.jpg"))
        num_pdf = len(existing) + 1
    else:
        num_pdf = args.start
        total = args.pages if args.pages else None
        with tqdm(desc="Downloading pages", unit="page", total=total, dynamic_ncols=True, disable=args.quiet) as pbar:
            with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as client:
                for i in range(args.start, 1001):
                    num = str(i).rjust(3, '0')
                    in_url = base_url + f"-{num}.jpg"
                    doc_name = new_name(in_url.rsplit('/', 1)[1])
                    if len(doc_name) > 250:
                        doc_name = "The file has been renamed,because original file namois too long. Now name:" + Path(doc_name).suffix

                    dest = img_path / doc_name
                    if dest.exists() and is_valid_jpeg(dest):
                        if not args.quiet:
                            tqdm.write(f"Skipping {doc_name} (already downloaded)")
                        num_pdf = i + 1
                        pbar.update(1)
                        continue

                    if dest.exists():
                        tqdm.write(f"Replacing corrupt file: {doc_name}")

                    pbar.set_postfix_str(doc_name)
                    try:
                        response = fetch_with_retry(client, in_url, args.retries, args.backoff)
                    except (httpx.TimeoutException, httpx.ReadError, httpx.ConnectError) as e:
                        tqdm.write(f"Download {doc_name} failed after {args.retries} attempts ({e.__class__.__name__}). Skipping.")
                        continue

                    if response.status_code == 404:
                        tqdm.write(f"Page {num} not found (404). Download finished. Packing into pdf...")
                        num_pdf = i
                        break

                    if response.status_code != 200:
                        tqdm.write(f"Unexpected status {response.status_code} for {doc_name}. Skipping.")
                        continue

                    content_type = response.headers.get("content-type", "")
                    if not content_type.startswith("image/") or not response.content:
                        tqdm.write(f"Invalid content for {doc_name} (content-type: {content_type}). Skipping.")
                        continue

                    tmp = dest.with_suffix(".tmp")
                    _current_tmp = tmp
                    tmp.write_bytes(response.content)
                    tmp.rename(dest)
                    _current_tmp = None
                    pbar.update(1)

                    if args.delay:
                        time.sleep(args.delay * (0.5 + random.random()))

    if not args.no_pdf:
        img2pdf(img_path, base_name, num_pdf, output, args.quiet)
        print(f"PDF saved to {output}")
