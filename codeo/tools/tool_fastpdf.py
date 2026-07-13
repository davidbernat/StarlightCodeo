# maintainer: starlight.ai
# author: starlight.ai
# version v0.0.5
# purpose: fastest PDF text + optional image extraction to markdown chunks
# changelog:
#  v0.0.1 ==> initial version
#  v0.0.2 ==> updated expectations for PDF/Book and extraction of images
#  v0.0.3 ==> added metadata to print incl. runtime now, original filename, tool name (this file)
#  v0.0.4 ==> rename to tool_fastpdf.py; add --output dir, --images flag, YAML frontmatter per page
#  v0.0.5 ==> --end_i defaults to doc.page_count (full document) instead of start_i + 5

# Design rationale:
# - Uses pymupdf4llm.to_markdown(page_chunks=True) for page-level markdown extraction
# - Uses page index, not page number, for range specification (start_i / end_i)
# - DEFAULT_DPI_PDF = 300 matches PyMuPDF default render resolution
# - Future: adopt RemarkableOCR token-level data format
#   (https://github.com/markelwin/RemarkableOCR) as unified PDF/image pipeline,
#   where pdf → image → data replaces to_markdown for token-position extraction
# - Future: extract images; potential with analysis of images, etc.
#    flag for pdf/pjeg and set dpi to high value without flag
#
# notes: requires pip install pymupdf4llm, fitz
import pymupdf4llm
import fitz
import argparse
import datetime
import yaml
from pathlib import Path

DEFAULT_DPI_PDF = 300
DEFAULT_DPI_FMT = ".jpg"


def main():
    parser = argparse.ArgumentParser(description="fastest PDF text + optional image extraction")
    parser.add_argument("pdf_path", help="path to pdf file")
    parser.add_argument("--start_i", type=int, default=0, help="start page index (0-based, inclusive)")
    parser.add_argument("--end_i", type=int, default=None, help="end page index (0-based, exclusive)")
    parser.add_argument("--output", required=True, help="output directory for results")
    parser.add_argument("--images", action="store_true", default=False, help="render pages as high-res images")
    args = parser.parse_args()

    now = datetime.datetime.now().strftime("%Y%m%d.%H%M%S")
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(args.pdf_path)
    end_i = args.end_i if args.end_i is not None else doc.page_count
    pages = list(range(args.start_i, end_i))
    chunks = pymupdf4llm.to_markdown(doc, pages=pages, page_chunks=True,
                                     force_ocr=False, write_images=False, dpi=DEFAULT_DPI_PDF)

    base_frontmatter = {
        "maintainer": "starlight.ai",
        "tool": "tool_fastpdf.py",
        "now": now,
        "original": {
            "filename": Path(args.pdf_path).name,
        },
    }

    for i, c in enumerate(chunks):
        page_idx = args.start_i + i

        fm = dict(base_frontmatter)
        fm["original"]["page_idx"] = page_idx

        md_path = output_dir / f"fastpdf.page.{page_idx:04d}.md"
        md_content = f"---\n{yaml.dump(fm, default_flow_style=False).strip()}\n---\n\n{c['text']}"
        md_path.write_text(md_content)
        print(f"[fastpdf] page={page_idx} to markdown")

        if args.images:
            img_path = output_dir / f"fastpdf.page.{page_idx:04d}{DEFAULT_DPI_FMT}"
            page = doc[page_idx]
            pix = page.get_pixmap(dpi=DEFAULT_DPI_PDF)
            pix.save(str(img_path))
            print(f"[fastpdf] page={page_idx} to {DEFAULT_DPI_FMT}")

    print(f"[fastpdf] done — {len(chunks)} pages to {output_dir}")


if __name__ == "__main__":
    main()
