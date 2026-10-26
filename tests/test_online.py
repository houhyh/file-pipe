"""Explicit opt-in URL integration. No paid search or LLM requests."""
import os

import pytest

from office_synth.retrieval import acquire
from office_synth.storage import read_json


@pytest.mark.online
@pytest.mark.skipif(os.environ.get("OFFICE_SYNTH_TEST_ONLINE") != "1", reason="Set OFFICE_SYNTH_TEST_ONLINE=1 to permit real source downloads")
def test_real_html_and_pdf(pipeline):
    urls = [
        "https://controller.vpfa.fsu.edu/services/accounts-payable/payments-with-purchase-order/invoice-submission",
        "https://www.honeywell.com/content/dam/honeywellbt/en/documents/downloads/hon-corp-supplier-3-way-match-website.pdf",
    ]
    for url in urls:
        record = acquire(url, pipeline)
        assert record.download_status == record.parse_status == "succeeded", record.error
        assert record.raw_hash and record.content_hash
        assert read_json(pipeline.root / record.parsed_path)["units"]
