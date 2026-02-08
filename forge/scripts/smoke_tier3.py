import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath("."))

from axm_forge.models.claims import ClaimGenContext
from axm_forge.models.types import Chunk, Locator, TextSpan

# ensure generator registered
from axm_forge.extraction.tiers.tier3_llm import extract_tier3_claims
import axm_forge.extraction.tiers.tier3_llm as t3_module


def test_tier3_integration():
    print(">>> Starting Tier 3 Smoke Test...")

    # Keep this comfortably above the Tier 3 minimum chunk length to ensure
    # the adapter runs even in mock mode.
    mock_text = (
        "The quick brown fox jumps over the lazy dog. "
        "This sentence exists solely to push the chunk length above the minimum threshold."
    )
    mock_chunk = Chunk(
        chunk_id="chk_123",
        chunk_type="prose",
        locator=Locator(kind="test", block_id="1"),
        text_span=TextSpan(artifact="extracted_text", start=0, end=len(mock_text)),
        text=mock_text,
    )

    ctx = ClaimGenContext(
        doc_id="doc_test",
        extracted_text=mock_text,
        chunks=[mock_chunk],
        entities=MagicMock(),
        metrics={
            "enable_llm": True,
            # Use a non-mock backend here so the adapter exercises the vendored
            # executor path (which we stub below).
            "llm_backend": "ollama",
            "llm_model": "test-model",
        },
    )

    mock_executor = MagicMock()
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.data = [
        {
            "subject": "fox",
            "predicate": "jumps_over",
            "object": "dog",
            "value": None,
            "quote": "jumps over",
            "confidence": 0.99,
        }
    ]
    mock_executor.return_value = mock_result

    original_vendor = t3_module._vendor_available
    original_get = getattr(t3_module, "get_executor", None)

    # force vendor available and monkeypatch get_executor call site by patching inside function import:
    t3_module._vendor_available = lambda: True

    # patch import resolution by inserting dummy module objects
    class DummyReq:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    # monkeypatch by setting attributes used in extract_tier3_claims when it imports
    sys.modules.setdefault("axm_forge.vendor.axm_v05.executor", MagicMock(get_executor=lambda **kwargs: mock_executor))
    sys.modules.setdefault("axm_forge.vendor.axm_v05.parser", MagicMock(LLMRequest=DummyReq))

    try:
        print(">>> Running extract_tier3_claims...")
        claims = extract_tier3_claims(ctx)
        assert len(claims) == 1, f"Expected 1 claim, got {len(claims)}"
        c = claims[0]
        print(f"    Claim ID: {c.claim_id}")
        print(f"    Predicate: {c.predicate}")
        print(f"    Provenance: {c.provenance}")
        assert c.source_spans[0].snippet == "jumps over"
        print(">>> SUCCESS")
    finally:
        t3_module._vendor_available = original_vendor


if __name__ == "__main__":
    test_tier3_integration()
