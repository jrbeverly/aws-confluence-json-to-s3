import html
import json
import os

from atlassian import Confluence

TEST_OBJECT = {
    "with_quote": 'say "hello"',
    "with_lt_gt_amp": "a < b > c & d",
    "with_newline": "line one\nline two",
    "unicode": "café résumé",
    "literal_entity": "&amp;",
}
LABELS = ["path=security", "owner=platform", "Path=Reports/Q3", "Owner=Mixed Case", "plain"]

confluence = Confluence(
    url=os.environ["ATLASSIAN_URL"],
    username=os.environ["ATLASSIAN_EMAIL"],
    password=os.environ["ATLASSIAN_API_TOKEN"],
    cloud=True,
)

original = json.dumps(TEST_OBJECT, ensure_ascii=True, separators=(",", ":"))
page = confluence.create_page(
    space=os.environ["CONFLUENCE_SPACE"],
    title="aws-confluence-json-to-s3 probe (delete me)",
    body="<p>" + html.escape(original, quote=False) + "</p>",
    parent_id=os.environ["CONFLUENCE_PARENT_PAGE_ID"],
    representation="storage",
)
try:
    for label in LABELS:
        confluence.set_page_label(page["id"], label)
    stored = confluence.get_page_by_id(page["id"], expand="body.storage")["body"]["storage"]["value"]
    print("stored:", stored)
    print("round-trip identical:", html.unescape(stored.strip()[3:-4]) == original)
    print("labels:", [r["name"] for r in confluence.get_page_labels(page["id"])["results"]])
finally:
    confluence.remove_page(page["id"])
    confluence.remove_page(page["id"], status="trashed")
