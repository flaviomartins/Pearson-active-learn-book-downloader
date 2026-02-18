# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python CLI tool for downloading Pearson Active Learning textbook images and combining them into a PDF. It works by sequentially downloading numbered `.jpg` pages from a base URL and then merging them with pikepdf.

## Dependencies

```bash
pip install pikepdf Pillow httpx[http2]
```

Or via pyproject.toml:

```bash
pip install -e .
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
| `--delay`/`-d` | `0.5` | Delay in seconds between requests (with ±50% jitter) |
| `--retries` | `3` | Max retries on transient errors |
| `--backoff` | `2.0` | Initial backoff in seconds, doubled each retry |
| `--no-pdf` | off | Skip PDF generation after downloading |

Images are saved to `download/<name>/` (created automatically). Downloads stop on a 404 response, after which `img2pdf()` is called automatically. Already-downloaded pages are skipped after JPEG integrity verification.

## Architecture

Single script `main.py` with these functions:

- **`new_name(title)`** — sanitizes filenames by replacing special characters with underscores, preserving dashes.
- **`is_valid_jpeg(path)`** — validates a file is a readable JPEG using `Image.verify()`.
- **`fetch_with_retry(client, url, delay, max_retries, backoff)`** — fetches a URL with retries and exponential backoff on transient network errors, 5xx responses, and 429 rate limits. Applies jitter (±50%) to the inter-request delay.
- **`img2pdf(img_path, name, num, output)`** — iterates downloaded JPGs, embeds each as a DCTDecode image XObject in a pikepdf page, and saves the output PDF. Pillow is used to read image dimensions and colour mode (RGB/CMYK/grayscale).
