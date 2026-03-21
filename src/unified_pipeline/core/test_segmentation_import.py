"""
Test segmentation import fix

Quick test to verify we can import and call segment_word_cv_chunked
"""

import sys
from pathlib import Path

# Add parent directory to path
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

print(f"✓ Added {parent_dir} to sys.path")

# Try importing
try:
    from segmentation.word_chunked import segment_word_cv_chunked
    print("✓ Successfully imported segment_word_cv_chunked")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Try segmenting a small test file
word_file = Path('/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project/data/sample_cvs/word/2025_Denckla_Cv.docx')
output_file = Path('test_denckla_segmented.json')

if not word_file.exists():
    print(f"✗ Test file not found: {word_file}")
    sys.exit(1)

print(f"✓ Found test file: {word_file.name}")
print("→ Attempting segmentation...")

try:
    result = segment_word_cv_chunked(str(word_file), str(output_file))

    num_groups = result.get('meta', {}).get('num_top_level_groups', 0)
    num_entries = result.get('meta', {}).get('total_entries', 0)

    print(f"✓ Segmentation successful!")
    print(f"  - {num_groups} top-level groups")
    print(f"  - {num_entries} total entries")
    print(f"  - Output: {output_file}")

except Exception as e:
    print(f"✗ Segmentation failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n✅ All tests passed! Import fix is working.")
