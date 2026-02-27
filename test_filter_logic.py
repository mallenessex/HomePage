import sys
import os

# Add V2 to path
sys.path.append(os.path.abspath("V2"))

from app.content_filter import is_content_safe
from app.config import settings

def test_logic():
    print(f"Forbidden words: {settings.FORBIDDEN_WORDS}")
    
    # 1. Safe
    safe_text = "Hello world, what a nice day!"
    is_safe, word = is_content_safe(safe_text)
    print(f"Safe check: {is_safe}, {word}")
    assert is_safe == True
    
    # 2. Unsafe
    unsafe_text = "You are being very naughty today."
    is_safe, word = is_content_safe(unsafe_text)
    print(f"Unsafe check ('naughty'): {is_safe}, {word}")
    assert is_safe == False
    assert word == "naughty"
    
    # 3. Substring (should be safe)
    substring_text = "The prudent person is careful."
    is_safe, word = is_content_safe(substring_text)
    print(f"Substring check ('rude' in 'prudent'): {is_safe}, {word}")
    assert is_safe == True
    
    # 4. Punctuation
    punc_text = "Don't be Mean!!!"
    is_safe, word = is_content_safe(punc_text)
    print(f"Punctuation check ('mean'): {is_safe}, {word}")
    assert is_safe == False
    assert word == "mean"

    print("\nLOGIC TEST PASSED!")

if __name__ == "__main__":
    test_logic()
