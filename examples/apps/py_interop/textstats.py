# A local Python helper used from Klar via `extern`.
from collections import Counter


def top_words(text, n):
    words = [w.strip(".,!?;:").lower() for w in text.split()]
    return [{"word": w, "count": c} for w, c in Counter(w for w in words if w).most_common(n)]
