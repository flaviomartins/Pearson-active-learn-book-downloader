# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python CLI tool for downloading Pearson Active Learning textbook images and combining them into a PDF. It works by sequentially downloading numbered `.jpg` pages from a base URL and then merging them with pikepdf.

## Dependencies

```bash
pip install pikepdf Pillow httpx[http2] tqdm
```

With browser cookie extraction support:

```bash
pip install pikepdf Pillow httpx[http2] tqdm browser-cookie3
```

Or via pyproject.toml:

```bash
pip install -e .           # core deps
pip install -e ".[browser]"  # with browser-cookie3
```

## Running

```bash
python main.py <url> [options]
```

`url` is the base image URL with the page suffix stripped:

```bash
python main.py https://www.pearsonactivelearn.com/.../images/iAL_EMC_Psychology_68068
```

| Argument | Default | Description |
| --- | --- | --- |
| `url` | required | Base image URL without page suffix |
| `--output`/`-o` | `download/<name>/<name>.pdf` | Output PDF path |
| `--start`/`-s` | `1` | Start from this page number (for resuming) |
| `--pages`/`-p` | — | Expected total pages (enables ETA in progress bar) |
| `--delay`/`-d` | `0.5` | Delay in seconds between requests (with ±50% jitter) |
| `--retries` | `3` | Max retries on transient errors |
| `--backoff` | `2.0` | Initial backoff in seconds, doubled each retry |
| `--no-pdf` | off | Skip PDF generation after downloading |
| `--pdf-only` | off | Skip downloading; build PDF from existing images |
| `--quiet`/`-q` | off | Suppress per-page messages in terminal (log file unaffected) |
| `--log-file` | — | Write log output to this file |
| `--browser` | — | Auto-extract cookies from this browser (`chrome`, `firefox`, `edge`, `brave`, `chromium`, `opera`, `opera_gx`, `vivaldi`, `librewolf`); requires `browser-cookie3` |
| `--cookies` | — | Cookie header string (e.g. `'key=value; key2=value2'`) |
| `--cookie-file` | — | Path to a Netscape-format cookie file |

Images are saved to `download/<name>/` (created automatically). Downloads stop on a 404 response or after `MAX_CONSECUTIVE_FAILURES` (10) consecutive failures, after which `img2pdf()` is called automatically. Already-downloaded pages are skipped after JPEG integrity verification.

### Authentication

The site requires a login. Before the download loop starts, a pre-flight probe checks whether the first image URL is accessible. If it redirects to a non-image page, the script exits immediately with a message pointing to the auth options.

Three ways to authenticate (can be combined; later sources override earlier ones):

1. `--browser chrome` — browser-cookie3 reads live session cookies from the browser; no manual steps needed as long as you are already logged in.
2. `--cookie-file cookies.txt` — Netscape-format file exported by a browser extension such as *Get cookies.txt LOCALLY* (Chrome/Firefox).
3. `--cookies 'k=v; ...'` — paste the `Cookie` header value from DevTools → Network → request headers.

## Architecture

Single script `main.py` with these functions:

- **`load_cookies(cookie_str, cookie_file, browser)`** — returns a merged cookie dict from a browser (via rookiepy), a Netscape cookie file, and/or a raw cookie string.
- **`is_valid_jpeg(path)`** — validates a file is a readable JPEG using `Image.verify()`.
- **`fetch_with_retry(client, url, tmp_path, max_retries, backoff)`** — streams a URL to `tmp_path` with retries and exponential backoff on transient network errors, 5xx responses, and 429 rate limits. Returns a sentinel `(302, headers)` when the server redirects to a non-image URL (auth redirect).
- **`_load_page(img_file)`** — reads a JPEG file and returns its bytes, dimensions, and pikepdf colorspace; used by `ThreadPoolExecutor` in `img2pdf`.
- **`img2pdf(img_path, name, num, output, quiet)`** — builds a PDF by loading images in parallel batches (`PDF_BATCH_SIZE=50`) with `ThreadPoolExecutor`, embedding each as a DCTDecode image XObject in a pikepdf page.
