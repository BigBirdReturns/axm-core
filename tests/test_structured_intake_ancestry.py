"""Regression witnesses retained from the executable pre-split AXM ancestor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "forge"))

from axm_forge.ingestion.extractors import extract


def test_xbrl_routes_to_tier_zero_candidates(tmp_path):
    source = tmp_path / "filing.xbrl"
    source.write_text(
        """
        <xbrli:xbrl
            xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:us="urn:example">
          <xbrli:context id="FY2026">
            <xbrli:entity>
              <xbrli:identifier>ACME</xbrli:identifier>
            </xbrli:entity>
          </xbrli:context>
          <us:Revenue contextRef="FY2026" unitRef="USD">5000000</us:Revenue>
        </xbrli:xbrl>
        """,
        encoding="utf-8",
    )

    document = extract(source)

    assert document.format == "xbrl"
    assert document.metadata == {"concept_count": 1}
    assert document.tier0_candidates == [
        {
            "subject": "ACME",
            "predicate": "revenue",
            "object": "5000000(USD)",
            "object_type": "literal:string",
            "tier": 0,
            "confidence": 1.0,
            "evidence": "Revenue: 5000000 (USD)",
            "locator": {"kind": "xml", "file_path": str(source)},
        }
    ]


def test_ical_routes_event_time_and_location_to_tier_zero(tmp_path):
    source = tmp_path / "close.ics"
    source.write_text(
        "\n".join(
            [
                "BEGIN:VCALENDAR",
                "BEGIN:VEVENT",
                "SUMMARY:Close Review",
                "DTSTART:20260731T090000Z",
                "DTEND:20260731T100000Z",
                "LOCATION:Finance",
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        ),
        encoding="utf-8",
    )

    document = extract(source)

    assert document.format == "ical"
    assert [row["predicate"] for row in document.tier0_candidates] == [
        "scheduled_at",
        "located_at",
    ]
    assert all(row["tier"] == 0 for row in document.tier0_candidates)
    assert all(row["confidence"] == 1.0 for row in document.tier0_candidates)


def test_rss_and_atom_route_items_to_tier_zero(tmp_path):
    rss_source = tmp_path / "updates.rss"
    rss_source.write_text(
        """
        <rss><channel><item>
          <title>Quarterly Update</title>
          <link>https://example.invalid/q</link>
          <pubDate>2026-07-23</pubDate>
        </item></channel></rss>
        """,
        encoding="utf-8",
    )
    atom_source = tmp_path / "updates.atom"
    atom_source.write_text(
        """
        <feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <title>Annual Update</title>
          <updated>2026-07-23T12:00:00Z</updated>
          <link href="https://example.invalid/a"/>
        </entry></feed>
        """,
        encoding="utf-8",
    )

    rss_document = extract(rss_source)
    atom_document = extract(atom_source)

    assert rss_document.format == "rss"
    assert rss_document.tier0_candidates[0]["subject"] == "Quarterly Update"
    assert rss_document.tier0_candidates[0]["object"] == "2026-07-23"
    assert atom_document.format == "rss"
    assert atom_document.tier0_candidates[0]["subject"] == "Annual Update"
    assert atom_document.tier0_candidates[0]["object"] == "2026-07-23T12:00:00Z"
