import html
import logging
import os

import boto3
from atlassian import Confluence

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _decode(storage_value):
    v = storage_value.strip()
    assert v.startswith("<p>") and v.endswith("</p>")
    return html.unescape(v[3:-4]).encode("utf-8")


def handler(event, context):
    secret_arn = os.environ["ATLASSIAN_SECRET_ARN"]
    atlassian_url = os.environ["ATLASSIAN_URL"]
    atlassian_email = os.environ["ATLASSIAN_EMAIL"]
    root_page_id = os.environ["CONFLUENCE_ROOT_PAGE_ID"]
    bucket = os.environ["S3_BUCKET"]

    token = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]

    confluence = Confluence(url=atlassian_url, username=atlassian_email, password=token, cloud=True)
    s3 = boto3.client("s3")

    count = 0
    for child in confluence.get_page_child_by_type(root_page_id):
        page_id = child["id"]
        logger.info("publishing page %s", page_id)

        page = confluence.get_page_by_id(page_id, expand="body.storage")
        labels = confluence.get_page_labels(page_id)["results"]
        metadata = dict(label["name"].split("=", 1) for label in labels if "=" in label["name"])
        body = _decode(page["body"]["storage"]["value"])
        path = metadata.get("path")

        s3.put_object(
            Bucket=bucket,
            Key=f"{path}/{page_id}.json" if path else f"{page_id}.json",
            Body=body,
            ContentType="application/json",
            Metadata={**metadata, "confluence-page-id": page_id},
        )
        count += 1

    logger.info("published %d page(s)", count)
