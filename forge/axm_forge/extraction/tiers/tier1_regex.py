from __future__ import annotations

import re
from typing import List

from axm_forge.models.claims import Claim, ClaimArg, ClaimGenContext, SourceSpan, make_claim_id, make_entity_id
from axm_forge.models.types import TextSpan
from axm_forge.extraction.registry import register_generator

_MONEY = re.compile(r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\w+\s+\d{1,2},\s+\d{4})\b")

@register_generator("tier1_regex")
def extract_tier1(ctx: ClaimGenContext) -> List[Claim]:
    out: List[Claim] = []
    doc_ent = make_entity_id(ctx.doc_id, "entity", "document")
    for chunk in ctx.chunks:
        if chunk.chunk_type != "prose":
            continue
        for m in _MONEY.finditer(chunk.text):
            snippet = m.group(1)
            span = TextSpan(artifact="extracted_text", start=m.start(1), end=m.end(1))
            source_span = SourceSpan(locator=chunk.locator, text_span=span, snippet=snippet)
            args = (ClaimArg(role="subject", entity_id=doc_ent),)
            predicate = "mentions_money"
            value = snippet
            claim_id = make_claim_id(ctx.doc_id, predicate, list(args), value, source_span, source_span.span_key)
            out.append(Claim(
                claim_id=claim_id,
                predicate=predicate,
                args=args,
                value=value,
                polarity="affirmed",
                conditions=(),
                source_spans=(source_span,),
                provenance={"tier": "tier1_regex", "kind": "money", "chunk_id": chunk.chunk_id},
            ))
        for m in _DATE.finditer(chunk.text):
            snippet = m.group(1)
            span = TextSpan(artifact="extracted_text", start=m.start(1), end=m.end(1))
            source_span = SourceSpan(locator=chunk.locator, text_span=span, snippet=snippet)
            args = (ClaimArg(role="subject", entity_id=doc_ent),)
            predicate = "mentions_date"
            value = snippet
            claim_id = make_claim_id(ctx.doc_id, predicate, list(args), value, source_span, source_span.span_key)
            out.append(Claim(
                claim_id=claim_id,
                predicate=predicate,
                args=args,
                value=value,
                polarity="affirmed",
                conditions=(),
                source_spans=(source_span,),
                provenance={"tier": "tier1_regex", "kind": "date", "chunk_id": chunk.chunk_id},
            ))
    return out
