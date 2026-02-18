import re
import io
import sys
import time
import random
import signal
import logging
import argparse
import itertools
import http.cookiejar
import httpx
import pikepdf
from pathlib import Path
from contextlib import contextmanager
from PIL import Image
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

_FALLBACK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36")

try:
    from fake_useragent import UserAgent as _UserAgent
    _ua = _UserAgent(fallback=_FALLBACK_UA)
except Exception:
    _ua = None


def _random_ua():
    return _ua.random if _ua else _FALLBACK_UA


COLORSPACE_MAP = {
    'RGB':  pikepdf.Name.DeviceRGB,
    'CMYK': pikepdf.Name.DeviceCMYK,
    'L':    pikepdf.Name.DeviceGray,
}

PDF_BATCH_SIZE = 50
MAX_CONSECUTIVE_FAILURES = 10


def load_cookies(cookie_str=None, cookie_file=None, browser=None):
    """Return a dict of cookies from a browser, a header string, and/or a Netscape cookie file."""
    cookies = {}
    if browser:
        try:
            import browser_cookie3
            jar = getattr(browser_cookie3, browser)(domain_name='www.pearsonactivelearn.com')
            cookies.update({c.name: c.value for c in jar})
        except ImportError:
            log.error("browser-cookie3 is not installed. Run: pip install browser-cookie3")
            raise SystemExit(1)
    if cookie_str:
        for part in cookie_str.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                cookies[k.strip()] = v.strip()
    if cookie_file:
        jar = http.cookiejar.MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        for cookie in jar:
            cookies[cookie.name] = cookie.value
    return cookies

_current_tmp: Path | None = None

log = logging.getLogger(__name__)



def _sigint_handler(_sig, _frame):
    try:
        if _current_tmp and _current_tmp.exists():
            _current_tmp.unlink()
            log.warning(f"Interrupted. Removed incomplete file: {_current_tmp}")
    except Exception:
        pass
    raise SystemExit(1)


@contextmanager
def _track_tmp(path):
    global _current_tmp
    _current_tmp = path
    try:
        yield path
    finally:
        _current_tmp = None


def is_valid_jpeg(path):
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except (OSError, SyntaxError):
        return False


_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def fetch_with_retry(client, url, tmp_path, max_retries, backoff, ua, quiet=False):
    """Fetch url, streaming the body to tmp_path on success.

    Returns (status_code, headers). On 200, tmp_path is written.
    Returns (302, headers) if the server redirected to a non-image URL
    (indicates an auth redirect — caller should abort with a helpful message).
    Raises on exhausted retries for network errors.
    """
    current_ua = ua
    for attempt in range(1, max_retries + 1):
        try:
            with client.stream("GET", url, headers={"User-Agent": current_ua}, follow_redirects=True) as response:
                status = response.status_code
                headers = response.headers

                if status == 429:
                    response.read()
                    retry_after = int(headers.get("Retry-After", 5))
                    log.warning(f"Rate limited. Retrying in {retry_after}s...")
                    time.sleep(retry_after)
                    current_ua = _random_ua()
                    continue

                if status in (500, 502, 503, 504):
                    response.read()
                    if attempt == max_retries:
                        return status, headers
                    wait = backoff * 2 ** (attempt - 1)
                    log.warning(f"Server error {status}, retrying in {wait}s ({attempt}/{max_retries})...")
                    time.sleep(wait)
                    current_ua = _random_ua()
                    continue

                if status == 200:
                    final_ext = Path(response.url.path).suffix.lower()
                    if final_ext not in _IMAGE_EXTS:
                        response.read()
                        return 302, response.headers
                    content_length = int(headers.get("content-length", 0)) or None
                    with tqdm(total=content_length, unit="B", unit_scale=True,
                              unit_divisor=1024, desc=Path(url).name,
                              leave=False, dynamic_ncols=True, file=sys.stderr,
                              position=1, disable=quiet) as dl_pbar:
                        with tmp_path.open("wb") as f:
                            for chunk in response.iter_bytes(chunk_size=65536):
                                f.write(chunk)
                                dl_pbar.update(len(chunk))

                return status, headers

        except (httpx.TimeoutException, httpx.ReadError, httpx.ConnectError) as e:
            if attempt == max_retries:
                raise
            current_ua = _random_ua()
            wait = backoff * 2 ** (attempt - 1)
            log.warning(f"Network error ({e.__class__.__name__}), retrying in {wait}s ({attempt}/{max_retries})...")
            time.sleep(wait)

    return -1, {}


def _load_page(img_file):
    """Read a JPEG file and return (path, jpeg_bytes, width, height, colorspace)."""
    jpeg_data = img_file.read_bytes()
    with Image.open(io.BytesIO(jpeg_data)) as img:
        w, h = img.size
        colorspace = COLORSPACE_MAP.get(img.mode, pikepdf.Name.DeviceRGB)
    return img_file, jpeg_data, w, h, colorspace


def img2pdf(img_path, name, num, output, quiet):
    img_files = [img_path / f"{name}-{str(i).rjust(3, '0')}.jpg" for i in range(1, num)]
    existing = [(i + 1, f) for i, f in enumerate(img_files) if is_valid_jpeg(f)]

    pdf = pikepdf.Pdf.new()
    with tqdm(total=len(existing), desc="Building PDF", unit="page", disable=quiet, file=sys.stderr) as pbar:
        for batch_start in range(0, len(existing), PDF_BATCH_SIZE):
            batch = existing[batch_start:batch_start + PDF_BATCH_SIZE]
            with ThreadPoolExecutor() as executor:
                futures = {executor.submit(_load_page, f): page_num for page_num, f in batch}
                results = {}
                for future in as_completed(futures):
                    page_num = futures[future]
                    try:
                        results[page_num] = future.result()
                    except FileNotFoundError as e:
                        log.warning(f"Warning: {e.filename} not found, skipping.")

            for page_num in sorted(results):
                _, jpeg_data, w, h, colorspace = results.pop(page_num)

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
                page = pikepdf.Page(pikepdf.Dictionary(
                    Type=pikepdf.Name.Page,
                    MediaBox=pikepdf.Array([0, 0, w, h]),
                    Resources=pikepdf.Dictionary(
                        XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(image_xobj))
                    ),
                    Contents=pdf.make_indirect(pikepdf.Stream(pdf, content)),
                ))
                pdf.pages.append(page)
                pbar.update(1)

    pdf.save(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download a Pearson Active Learning book as a PDF.",
        epilog=(
            "Example:\n"
            "  Strip the page suffix from a URL found in your browser's developer tools:\n"
            "  https://resources.pearsonactivelearn.com/.../images/9781292244778-001.jpg\n"
            "  becomes:\n"
            "  %(prog)s https://resources.pearsonactivelearn.com/.../images/9781292244778\n"
            "\n"
            "Authentication:\n"
            "  The site requires a login. Choose one approach:\n"
            "    --browser chrome          auto-extract from a running browser session\n"
            "                              (install support: pip install rookiepy)\n"
            "    --cookie-file FILE        Netscape-format cookie file exported from your\n"
            "                              browser (e.g. via the 'Get cookies.txt LOCALLY'\n"
            "                              extension for Chrome/Firefox)\n"
            "    --cookies 'k=v; k2=v2'   paste the Cookie header value directly from\n"
            "                              browser DevTools → Network → request headers"
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
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress bars")
    parser.add_argument("--log-file", help="Write log output to this file")
    parser.add_argument("--browser",
                        choices=["chrome", "firefox", "edge", "brave",
                                 "chromium", "opera", "opera_gx", "vivaldi", "librewolf"],
                        help="Auto-extract cookies from this browser (requires: pip install browser-cookie3)")
    parser.add_argument("--cookies", help="Cookie string to send with requests (e.g. 'key=value; key2=value2')")
    parser.add_argument("--cookie-file", help="Path to a Netscape-format cookie file")
    args = parser.parse_args()

    if args.log_file:
        handler = logging.FileHandler(args.log_file)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    else:
        logging.disable(logging.CRITICAL)

    base_url = args.url.rstrip('/')
    base_name = base_url.rsplit('/', 1)[1]
    img_path = Path("download") / base_name
    img_path.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else img_path / f"{base_name}.pdf"

    signal.signal(signal.SIGINT, _sigint_handler)

    if args.pdf_only:
        nums = [int(m.group(1)) for f in img_path.glob(f"{base_name}-*.jpg")
                if is_valid_jpeg(f) and (m := re.search(r"-(\d+)\.jpg$", f.name))]
        num_pdf = max(nums) + 1 if nums else 1
    else:
        num_pdf = args.start
        total = args.pages if args.pages else None
        consecutive_failures = 0
        cookies = load_cookies(args.cookies, args.cookie_file, args.browser)
        session_ua = _random_ua()
        with httpx.Client(cookies=cookies, follow_redirects=True, timeout=30) as client:
            probe_url = base_url + f"-{str(args.start).rjust(3, '0')}.jpg"
            with client.stream("GET", probe_url, headers={"User-Agent": session_ua}, follow_redirects=True) as r:
                r.read()
                if r.status_code == 200 and Path(r.url.path).suffix.lower() not in _IMAGE_EXTS:
                    log.error(
                        f"Authentication required — image request was redirected to {r.url}. "
                        "Use --browser, --cookie-file, or --cookies to authenticate."
                    )
                    raise SystemExit(1)
            with tqdm(desc="Downloading pages", unit="page", total=total, dynamic_ncols=True, position=0, disable=args.quiet, file=sys.stderr) as pbar:
                for i in itertools.count(args.start):
                    num = str(i).rjust(3, '0')
                    in_url = base_url + f"-{num}.jpg"
                    doc_name = f"{base_name}-{num}.jpg"

                    dest = img_path / doc_name
                    if is_valid_jpeg(dest):
                        log.info(f"Skipping {doc_name} (already downloaded)")
                        num_pdf = i + 1
                        consecutive_failures = 0
                        pbar.update(1)
                        continue

                    if dest.exists():
                        log.warning(f"Replacing corrupt file: {doc_name}")

                    pbar.set_postfix_str(doc_name)
                    tmp = dest.with_suffix(".tmp")
                    with _track_tmp(tmp):
                        try:
                            status, headers = fetch_with_retry(client, in_url, tmp, args.retries, args.backoff, session_ua, args.quiet)
                        except (httpx.TimeoutException, httpx.ReadError, httpx.ConnectError) as e:
                            log.error(f"Download {doc_name} failed after {args.retries} attempts ({e.__class__.__name__}). Skipping.")
                            consecutive_failures += 1
                            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                                log.error(f"Aborting after {MAX_CONSECUTIVE_FAILURES} consecutive failures.")
                                break
                            continue

                        if status == 404:
                            log.info(f"Page {num} not found (404). Download finished. Packing into pdf...")
                            num_pdf = i
                            break

                        if status == 302:
                            log.error(
                                "Server redirected the image request to a non-image URL — "
                                "authentication is required. Use --browser, --cookie-file, "
                                "or --cookies to authenticate."
                            )
                            num_pdf = i
                            break

                        if status != 200:
                            log.warning(f"Unexpected status {status} for {doc_name}. Skipping.")
                            consecutive_failures += 1
                            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                                log.error(f"Aborting after {MAX_CONSECUTIVE_FAILURES} consecutive failures.")
                                break
                            continue

                        content_type = headers.get("content-type", "")
                        if not content_type.startswith("image/") or not tmp.exists():
                            log.warning(f"Invalid content for {doc_name} (content-type: {content_type}). Skipping.")
                            consecutive_failures += 1
                            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                                log.error(f"Aborting after {MAX_CONSECUTIVE_FAILURES} consecutive failures.")
                                break
                            continue

                        tmp.rename(dest)
                        num_pdf = i + 1
                        consecutive_failures = 0
                        pbar.update(1)

                    if args.delay:
                        time.sleep(args.delay * (0.5 + random.random()))

    if not args.no_pdf:
        img2pdf(img_path, base_name, num_pdf, output, args.quiet)
        log.info(f"PDF saved to {output}")
