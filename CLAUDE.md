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
cd "pearson active book downloader"
python main.py
```

The tool is interactive: it prompts for a base URL on each iteration, appending a zero-padded page number (e.g. `001.jpg`). The user must strip the page suffix from the URL before entering it. Downloads stop when a non-200 response is received, after which `combain2pdf.img2pdf()` is called automatically.

## Architecture

Single script `main.py` with three functions:

- **`new_name(title)`** — sanitizes filenames by replacing special characters with underscores.
- **`get_file_extension(filename)`** — returns the file extension.
- **`img2pdf(name, num)`** — iterates downloaded JPGs, embeds each as a DCTDecode image XObject in a pikepdf page, and saves `combined.pdf` in the working directory. Pillow is used to read image dimensions and colour mode (RGB/CMYK/grayscale).

The module-level code runs an interactive download loop (pages 1–1000) using a persistent `httpx.Client` session, saves images into `.\download\`, then calls `img2pdf()` on completion.

## Known Issues

- Paths are hard-coded with Windows separators (`.\download\\`). On macOS/Linux, the `download` directory must exist in the working directory and path separators need to be changed to work correctly.
- `main.py` requests the URL interactively on every loop iteration rather than once at the start — this is by design but may be confusing.
