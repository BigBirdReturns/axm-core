#!/usr/bin/env python3
"""Comprehensive test for Step 2: Text Density + Native Detection.

Tests:
1. Router analyzes PDF and computes signals
2. Text density is measured correctly
3. Native text layer is detected
4. Router makes correct tier decision (Tier 0)
5. Emitter produces valid source.txt and provenance.jsonl
6. Validator confirms output follows contract
"""

import sys
import json
from pathlib import Path
import tempfile

# Add forge to path
sys.path.insert(0, str(Path(__file__).parent.parent / "forge"))

from axm_forge.routing import Router, validate_emission


def test_step2_routing():
    """Test Step 2: Text Density + Native Detection."""
    
    # Use test fixture
    fixture_path = Path(__file__).parent / "fixtures" / "test_native.pdf"
    
    if not fixture_path.exists():
        print(f"❌ Test PDF not found: {fixture_path}")
        print("   Run: python tests/generate_test_pdf.py")
        return False
    
    print(f"Testing with: {fixture_path}\n")
    
    # Create temp output dir
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "output"
        
        try:
            # Create router
            router = Router(fixture_path)
            
            # Step 1: Analyze document
            print("1️⃣  Analyzing document...")
            signals_list = router.analyze_document()
            
            if not signals_list:
                print("❌ No signals computed")
                return False
            
            print(f"   ✅ Analyzed {len(signals_list)} page(s)")
            
            # Step 2: Check signals for first page
            print("\n2️⃣  Checking page signals...")
            signals = signals_list[0]
            print(f"   {signals}")
            
            # Validate signals
            if signals.text_density <= 0:
                print("   ❌ Text density is zero or negative")
                return False
            
            if not signals.has_native_text:
                print("   ❌ Native text layer not detected (should be present)")
                return False
            
            if signals.confidence_tier0 < 0.5:
                print(f"   ❌ Low confidence for Tier 0: {signals.confidence_tier0}")
                return False
            
            print(f"   ✅ Text density: {signals.text_density:.4f} chars/pt²")
            print(f"   ✅ Native text detected: {signals.has_native_text}")
            print(f"   ✅ Tier 0 confidence: {signals.confidence_tier0:.2f}")
            
            # Step 3: Route page
            print("\n3️⃣  Routing page...")
            decisions = router.route_page(1)
            
            if not decisions:
                print("   ❌ No routing decisions made")
                return False
            
            decision = decisions[0]
            print(f"   Decision: Tier {decision.tier}, confidence {decision.confidence:.2f}")
            print(f"   Reason: {decision.reason}")
            
            if decision.tier != 0:
                print(f"   ❌ Expected Tier 0, got Tier {decision.tier}")
                return False
            
            print("   ✅ Correctly routed to Tier 0 (native text)")
            
            # Step 4: Process document
            print("\n4️⃣  Processing document...")
            router.process_document(output_dir)
            
            # Check output files exist
            source_txt = output_dir / "source.txt"
            provenance_jsonl = output_dir / "provenance.jsonl"
            candidates_jsonl = output_dir / "candidates.jsonl"
            
            if not source_txt.exists():
                print("   ❌ source.txt not created")
                return False
            
            if not provenance_jsonl.exists():
                print("   ❌ provenance.jsonl not created")
                return False
            
            print("   ✅ source.txt created")
            print("   ✅ provenance.jsonl created")
            
            # Step 5: Validate output
            print("\n5️⃣  Validating output...")
            validation = validate_emission(output_dir)
            
            if not validation.valid:
                print("   ❌ Validation failed:")
                for error in validation.errors:
                    print(f"      - {error}")
                return False
            
            if validation.warnings:
                print("   ⚠️  Warnings:")
                for warning in validation.warnings:
                    print(f"      - {warning}")
            
            print("   ✅ Output validates correctly")
            
            # Step 6: Check content
            print("\n6️⃣  Checking content...")
            
            source_text = source_txt.read_text(encoding='utf-8')
            print(f"   Source text length: {len(source_text)} chars")
            
            if "Hemostatic" not in source_text:
                print("   ❌ Expected content not found in source.txt")
                return False
            
            print("   ✅ Content extracted correctly")
            
            # Check provenance
            provenance_lines = provenance_jsonl.read_text().strip().split('\n')
            print(f"   Provenance entries: {len(provenance_lines)}")
            
            first_entry = json.loads(provenance_lines[0])
            if first_entry.get("tier") != 0:
                print(f"   ❌ Expected tier 0, got tier {first_entry.get('tier')}")
                return False
            
            print(f"   ✅ Provenance tier: {first_entry.get('tier')}")
            print(f"   ✅ Provenance confidence: {first_entry.get('confidence'):.2f}")
            
            # Cleanup
            router.close()
            
            return True
            
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    print("=== Step 2: Text Density + Native Detection Test ===\n")
    
    success = test_step2_routing()
    
    if success:
        print("\n" + "="*50)
        print("✅ STEP 2 TEST PASSED")
        print("="*50)
        print("\nRouter correctly:")
        print("  • Measures text density")
        print("  • Detects native text layer")
        print("  • Computes Tier 0 confidence")
        print("  • Routes to appropriate tier")
        print("  • Emits valid source.txt and provenance.jsonl")
        print("  • Passes contract validation")
        sys.exit(0)
    else:
        print("\n" + "="*50)
        print("❌ STEP 2 TEST FAILED")
        print("="*50)
        sys.exit(1)
