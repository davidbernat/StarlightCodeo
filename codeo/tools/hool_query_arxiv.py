# maintainer: starlight.ai
# author: starlight.ai
# version v0.0.6
# purpose: CLI to fetch arXiv papers; uses ArxivAPI, outputs yaml to file
# changelog:
#  v0.0.1 ==> initial creation
#  v0.0.2 ==> renamed from arxiv.py to avoid shadowing
#  v0.0.3 ==> --since/--until CLI args
#  v0.0.4 ==> --query for search terms
#  v0.0.5 ==> refactored: ArxivApi handles the API, this is pure CLI wrapper
#  v0.0.6 ==> agentic.thirdparty.ArxivAPI import; --qboot → --output; added logger

# Design rationale:
# - PROGRAM python output protocol via --qboot <path>. Script writes structured
#   YAML to that file; QBoot reads it for >> $OUTPUT and AS coercion.
# - If --qboot is not provided, prints to stdout (backward compat).
# - All arXiv logic lives in ArxivApi.py — this file only parses args,
#   calls search, and serializes output.
#

import argparse
import logging
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from codeo.thirdparty.ArxivAPI import ArxivApi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch arXiv papers by date and/or search query")
    parser.add_argument("--since", help="start date yyyymmdd (default: until - 1 day)")
    parser.add_argument("--until", help="end date yyyymmdd (default: today)")
    parser.add_argument("--query", help="arXiv search terms")
    parser.add_argument("--output", required=True, help="path to write yaml output")
    args = parser.parse_args()

    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    until_str = args.until if args.until else today_str
    if args.since:
        since_str = args.since
    else:
        since_dt = (datetime.strptime(until_str, "%Y%m%d")
                    .replace(tzinfo=timezone.utc) - timedelta(days=1))
        since_str = since_dt.strftime("%Y%m%d")

    since = f"{since_str}0000"
    until = f"{until_str}2359"

    papers = ArxivApi.search(since, until, query=args.query)
    logger.info(f"[fetch-arxiv] papers={len(papers)} since={since_str} until={until_str} query={args.query}")

    output_str = yaml.dump([p.model_dump(mode="python") for p in papers],
                           default_flow_style=False, sort_keys=False, allow_unicode=True)
    Path(args.output).write_text(output_str)


if __name__ == "__main__":
    main()
