import html
import json
import os
import sys
from unittest import TestCase
from unittest.mock import MagicMock, patch

from atlassian import Confluence as _RealConfluence
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import handler

_ENV = {
    "ATLASSIAN_URL": "https://example.atlassian.net",
    "ATLASSIAN_EMAIL": "bot@example.com",
    "CONFLUENCE_ROOT_PAGE_ID": "111111111",
    "S3_BUCKET": "test-bucket",
    "ATLASSIAN_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:000000000000:secret:test",
}

_PAGE_ID = "123456789"
_JSON_STR = json.dumps({"example": "value", "n": 1}, ensure_ascii=True, separators=(",", ":"))
_STORAGE_VALUE = "<p>" + html.escape(_JSON_STR, quote=False) + "</p>"
_EXPECTED_BODY = _JSON_STR.encode("utf-8")

# Page IDs used by the SDK pagination tests.
_SDK_P1 = "page-aaa"
_SDK_P2 = "page-bbb"
# _links.next value returned by the first children HTTP response page.
_PAGINATION_NEXT = "/rest/api/content/111111111/child/page?start=50"


def _storage(obj):
    s = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return "<p>" + html.escape(s, quote=False) + "</p>"


def _clients(s3_error=None, sm_error=None):
    sm = MagicMock()
    if sm_error:
        sm.get_secret_value.side_effect = sm_error
    else:
        sm.get_secret_value.return_value = {"SecretString": "api-token-value"}
    s3 = MagicMock()
    if s3_error:
        s3.put_object.side_effect = s3_error
    return (lambda svc, **kw: sm if svc == "secretsmanager" else s3), sm, s3


def _sdk_fake_get(p1_labels=None, p2_labels=None):
    """
    Return a side_effect function for a real Confluence instance's .get method.

    Simulates two HTTP response pages for the children list (_links.next present
    on the first) so that the SDK's _get_paged loop is exercised rather than a
    pre-built result list being mocked.  Routes label and body requests by URL.

    Raises AssertionError if any child/page URL contains a child page ID,
    which would indicate an attempted grandchild enumeration.
    """
    p1_labels = p1_labels or [{"name": "path=alpha"}]
    p2_labels = p2_labels or [{"name": "path=beta"}]
    p1_body = _storage({"n": 1})
    p2_body = _storage({"n": 2})
    children_call_count = [0]

    def fake_get(url, **kwargs):
        if "child/page" in url:
            if _SDK_P1 in url or _SDK_P2 in url:
                raise AssertionError(f"grandchild enumeration attempted: {url!r}")
            idx = children_call_count[0]
            children_call_count[0] += 1
            if idx == 0:
                return {"results": [{"id": _SDK_P1}], "_links": {"next": _PAGINATION_NEXT}}
            return {"results": [{"id": _SDK_P2}], "_links": {}}
        if f"content/{_SDK_P1}/label" in url:
            return {"results": p1_labels}
        if f"content/{_SDK_P2}/label" in url:
            return {"results": p2_labels}
        if f"content/{_SDK_P1}" in url:
            return {"id": _SDK_P1, "body": {"storage": {"value": p1_body}}}
        if f"content/{_SDK_P2}" in url:
            return {"id": _SDK_P2, "body": {"storage": {"value": p2_body}}}
        raise AssertionError(f"unexpected SDK get call: {url!r}")

    return fake_get


class TestDecode(TestCase):
    """Direct round-trip tests for the agreed _decode operation."""

    def _roundtrip(self, obj):
        decoded = handler._decode(_storage(obj))
        expected = json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(decoded, expected)

    def test_quotes(self):
        self._roundtrip({"k": 'say "hello"'})

    def test_unicode(self):
        self._roundtrip({"k": "café résumé"})

    def test_newline(self):
        self._roundtrip({"k": "line one\nline two"})

    def test_less_than(self):
        self._roundtrip({"k": "a < b"})

    def test_greater_than(self):
        self._roundtrip({"k": "a > b"})

    def test_ampersand(self):
        self._roundtrip({"k": "cats & dogs"})

    def test_literal_entity(self):
        self._roundtrip({"k": "&amp;"})


class TestHandler(TestCase):

    def test_one_page_exact_s3_request(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([{"id": _PAGE_ID}])
        confluence.get_page_by_id.return_value = {
            "id": _PAGE_ID,
            "body": {"storage": {"value": _STORAGE_VALUE}},
        }
        confluence.get_page_labels.return_value = {
            "results": [{"name": "path=security"}, {"name": "owner=platform"}]
        }

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            handler.handler({}, None)

        sm.get_secret_value.assert_called_once_with(SecretId=_ENV["ATLASSIAN_SECRET_ARN"])
        s3.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="security/123456789.json",
            Body=_EXPECTED_BODY,
            ContentType="application/json",
            Metadata={"path": "security", "owner": "platform", "confluence-page-id": _PAGE_ID},
        )

    def test_confluence_page_id_overrides_source_label(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([{"id": _PAGE_ID}])
        confluence.get_page_by_id.return_value = {
            "id": _PAGE_ID,
            "body": {"storage": {"value": _STORAGE_VALUE}},
        }
        confluence.get_page_labels.return_value = {
            "results": [
                {"name": "path=security"},
                {"name": "confluence-page-id=wrong-id"},
            ]
        }

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            handler.handler({}, None)

        meta = s3.put_object.call_args.kwargs["Metadata"]
        self.assertEqual(meta["confluence-page-id"], _PAGE_ID)

    def test_label_split_once_at_first_equals(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([{"id": _PAGE_ID}])
        confluence.get_page_by_id.return_value = {
            "id": _PAGE_ID,
            "body": {"storage": {"value": _STORAGE_VALUE}},
        }
        confluence.get_page_labels.return_value = {
            "results": [{"name": "path=security"}, {"name": "env=prod=extra"}]
        }

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            handler.handler({}, None)

        meta = s3.put_object.call_args.kwargs["Metadata"]
        self.assertEqual(meta["env"], "prod=extra")

    def test_multiple_labels_in_metadata(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([{"id": _PAGE_ID}])
        confluence.get_page_by_id.return_value = {
            "id": _PAGE_ID,
            "body": {"storage": {"value": _STORAGE_VALUE}},
        }
        confluence.get_page_labels.return_value = {
            "results": [
                {"name": "path=data"},
                {"name": "type=policy"},
                {"name": "owner=platform"},
                {"name": "environment=prod"},
            ]
        }

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            handler.handler({}, None)

        meta = s3.put_object.call_args.kwargs["Metadata"]
        self.assertEqual(meta["type"], "policy")
        self.assertEqual(meta["owner"], "platform")
        self.assertEqual(meta["environment"], "prod")
        self.assertIn("confluence-page-id", meta)

    def test_no_path_label_and_plain_label(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([{"id": _PAGE_ID}])
        confluence.get_page_by_id.return_value = {
            "id": _PAGE_ID,
            "body": {"storage": {"value": _STORAGE_VALUE}},
        }
        confluence.get_page_labels.return_value = {
            "results": [{"name": "plain"}, {"name": "owner=platform"}]
        }

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            handler.handler({}, None)

        kwargs = s3.put_object.call_args.kwargs
        self.assertEqual(kwargs["Key"], f"{_PAGE_ID}.json")
        self.assertEqual(kwargs["Metadata"], {"owner": "platform", "confluence-page-id": _PAGE_ID})

    def test_s3_error_propagates(self):
        error = ClientError({"Error": {"Code": "NoSuchBucket", "Message": ""}}, "PutObject")
        boto3_side, sm, s3 = _clients(s3_error=error)
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([{"id": _PAGE_ID}])
        confluence.get_page_by_id.return_value = {
            "id": _PAGE_ID,
            "body": {"storage": {"value": _STORAGE_VALUE}},
        }
        confluence.get_page_labels.return_value = {"results": [{"name": "path=security"}]}

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            with self.assertRaises(ClientError):
                handler.handler({}, None)

    def test_empty_root_no_s3_calls(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([])

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            handler.handler({}, None)

        s3.put_object.assert_not_called()

    def test_repeat_invocation_identical_keys(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.side_effect = [
            iter([{"id": _PAGE_ID}]),
            iter([{"id": _PAGE_ID}]),
        ]
        confluence.get_page_by_id.return_value = {
            "id": _PAGE_ID,
            "body": {"storage": {"value": _STORAGE_VALUE}},
        }
        confluence.get_page_labels.return_value = {"results": [{"name": "path=security"}]}

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            handler.handler({}, None)
            handler.handler({}, None)

        self.assertEqual(s3.put_object.call_count, 2)
        k0 = s3.put_object.call_args_list[0].kwargs["Key"]
        k1 = s3.put_object.call_args_list[1].kwargs["Key"]
        self.assertEqual(k0, k1)

    def test_secret_failure_propagates(self):
        error = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": ""}},
            "GetSecretValue",
        )
        boto3_side, sm, s3 = _clients(sm_error=error)
        confluence = MagicMock()

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            with self.assertRaises(ClientError):
                handler.handler({}, None)

        s3.put_object.assert_not_called()

    def test_confluence_failure_propagates(self):
        boto3_side, sm, s3 = _clients()
        confluence = MagicMock()
        confluence.get_page_child_by_type.side_effect = Exception("upstream error")

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            with self.assertRaises(Exception):
                handler.handler({}, None)

        s3.put_object.assert_not_called()

    def test_partial_failure_earlier_pages_written(self):
        P1 = "aaa111"
        P2 = "bbb222"
        calls = [0]

        def put_side_effect(**kwargs):
            calls[0] += 1
            if calls[0] > 1:
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": ""}}, "PutObject"
                )

        boto3_side, sm, s3 = _clients()
        s3.put_object.side_effect = put_side_effect
        confluence = MagicMock()
        confluence.get_page_child_by_type.return_value = iter([{"id": P1}, {"id": P2}])
        confluence.get_page_by_id.side_effect = [
            {"id": P1, "body": {"storage": {"value": _STORAGE_VALUE}}},
            {"id": P2, "body": {"storage": {"value": _STORAGE_VALUE}}},
        ]
        confluence.get_page_labels.return_value = {"results": [{"name": "path=data"}]}

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence):
            with self.assertRaises(ClientError):
                handler.handler({}, None)

        self.assertEqual(s3.put_object.call_count, 2)
        self.assertEqual(
            s3.put_object.call_args_list[0].kwargs["Key"], f"data/{P1}.json"
        )


class TestSdkPagination(TestCase):
    """
    Uses a real Confluence instance with .get mocked to exercise the SDK's
    _get_paged loop (following _links.next) rather than mocking an already-
    complete result list.
    """

    def _confluence(self):
        return _RealConfluence(
            url=_ENV["ATLASSIAN_URL"], username="x", password="x", cloud=True
        )

    def test_two_child_pages_both_published(self):
        fake_get = _sdk_fake_get()
        confluence = self._confluence()
        boto3_side, sm, s3 = _clients()

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence), \
             patch.object(confluence, "get", side_effect=fake_get):
            handler.handler({}, None)

        self.assertEqual(s3.put_object.call_count, 2)
        keys = {c.kwargs["Key"] for c in s3.put_object.call_args_list}
        self.assertIn(f"alpha/{_SDK_P1}.json", keys)
        self.assertIn(f"beta/{_SDK_P2}.json", keys)

    def test_no_grandchild_enumeration(self):
        # _sdk_fake_get raises AssertionError if any child/page URL contains P1 or P2.
        fake_get = _sdk_fake_get()
        confluence = self._confluence()
        boto3_side, sm, s3 = _clients()

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence), \
             patch.object(confluence, "get", side_effect=fake_get):
            handler.handler({}, None)

        self.assertEqual(s3.put_object.call_count, 2)

    def test_multiple_labels_on_later_page_child(self):
        p2_labels = [
            {"name": "path=reports"},
            {"name": "type=policy"},
            {"name": "owner=sec"},
        ]
        fake_get = _sdk_fake_get(p2_labels=p2_labels)
        confluence = self._confluence()
        boto3_side, sm, s3 = _clients()

        with patch.dict(os.environ, _ENV), \
             patch("boto3.client", side_effect=boto3_side), \
             patch("handler.Confluence", return_value=confluence), \
             patch.object(confluence, "get", side_effect=fake_get):
            handler.handler({}, None)

        p2_put = next(
            c for c in s3.put_object.call_args_list if _SDK_P2 in c.kwargs["Key"]
        )
        meta = p2_put.kwargs["Metadata"]
        self.assertEqual(meta["type"], "policy")
        self.assertEqual(meta["owner"], "sec")
        self.assertEqual(meta["path"], "reports")
