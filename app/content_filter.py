import re
from .config import settings

def is_content_safe(text: str) -> tuple[bool, str]:
    """
    Checks if the content contains any forbidden words.
    Returns (is_safe, found_word).
    """
    if not text:
        return True, ""
        
    # Standardize text for checking (lowercase, remove some punctuation)
    clean_text = re.sub(r'[^\w\s]', '', text.lower())
    
    for word in settings.FORBIDDEN_WORDS:
        # Use word boundaries to avoid matching fragments (e.g. 'bad' in 'badge')
        pattern = rf"\b{re.escape(word.lower())}\b"
        if re.search(pattern, clean_text):
            return False, word
            
    return True, ""
