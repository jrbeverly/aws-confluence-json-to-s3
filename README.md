# AWS Confluence JSON to S3

> [!WARNING]
> **AI-authored:** This change was autonomously planned and implemented by an AI software factory from a human-authored specification, with possible subsequent human review or modification.

Tests a Lambda that reads JSON bodies from the child pages of one Confluence page and writes each to S3, using the page's `key=value` labels as object metadata and the `path` label as the key prefix.

```sh
python3 -m unittest discover -s tests     # needs atlassian-python-api==3.41.21 and boto3
bash package.sh
export TF_VAR_atlassian_url=... TF_VAR_atlassian_email=... TF_VAR_atlassian_api_token=... TF_VAR_confluence_root_page_id=...
terraform init && terraform apply
aws lambda invoke --function-name confluence-json-to-s3 out.json
terraform destroy
```

`probe.py` creates one page under `CONFLUENCE_PARENT_PAGE_ID`, checks the body and label round-trip, and deletes it.

## Notes

- Confluence Cloud accepts labels containing `=` (`path=security`) and `/` (`path=reports/q3`) and returns them unchanged.
- Labels are lowercased (`Path=Reports/Q3` -> `path=reports/q3`), and a space splits one label into two (`Owner=Mixed Case` -> `owner=mixed`, `case`), so values must be lowercase with no spaces.
- A page can hold both `path=a` and `path=b`; the handler keeps whichever comes last.
- Confluence rewrites `"` in stored bodies as `&quot;`. Posting `<p>` + `html.escape(json.dumps(obj, ensure_ascii=True))` + `</p>` and reading it back with `html.unescape` still gives the original JSON byte for byte.
- `atlassian-python-api` 3.41.21: `get_page_child_by_type(id)` paginates automatically only when called without `start`/`limit`.
- The first version put a leading `/` in the key when a page had no `path` label, and crashed on any label without `=`. Both are fixed: such pages go to `<page-id>.json`, and labels without `=` are skipped.
- Live run: 3 child pages became `security/<id>.json`, `reports/q3/<id>.json` and `<id>.json` with the label metadata plus `confluence-page-id`. A cold invoke took 5.1 s and used 105 of 128 MB.
- The schedule (`rate(5 minutes)`) is created in the `DISABLED` state and was not exercised.
