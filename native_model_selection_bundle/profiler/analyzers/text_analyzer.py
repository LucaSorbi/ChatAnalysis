"""
profiler/analyzers/text_analyzer.py
Analyses TEXT properties of message body values.
Input: list of raw strings. Output: aggregate statistics only.
Never stores the actual text content.
"""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field

EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF\U0001F900-\U0001F9FF"
    "\U00002600-\U000026FF]+", flags=re.UNICODE)
PHONE_RE = re.compile(r"\+?\d[\d\s\-]{7,}\d")
URL_RE   = re.compile(r"https?://\S+|www\.\S+")
MEDIA_PH = re.compile(
    r"^\s*(<media omitted>|<immagine omessa>|<audio omesso>|"
    r"\[image\]|\[audio\]|\[video\]|null|none)\s*$", re.I)

@dataclass
class TextProfile:
    total_messages: int
    empty_messages: int
    avg_length_chars: float
    max_length_chars: int
    min_length_chars: int
    messages_with_emoji: int
    messages_with_urls: int
    messages_with_phone_numbers: int
    messages_with_newlines: int
    messages_with_only_media_placeholder: int
    encoding_issues_count: int
    top_emoji: list
    length_distribution: dict
    notes: list = field(default_factory=list)

def analyze(texts: list) -> TextProfile:
    if not texts:
        return TextProfile(0,0,0,0,0,0,0,0,0,0,0,[],{})
    total = len(texts)
    empty = emoji_count = url_count = phone_count = nl_count = media_ph = enc_issues = 0
    lengths = []; emoji_counter: Counter = Counter()
    buckets = {"0": 0, "1-50": 0, "51-200": 0, "201-500": 0,
               "501-1000": 0, "1001-5000": 0, ">5000": 0}
    for t in texts:
        if not t or not t.strip():
            empty += 1; continue
        if "\ufffd" in t: enc_issues += 1
        l = len(t); lengths.append(l)
        if EMOJI_RE.search(t):
            emoji_count += 1
            for ch in EMOJI_RE.findall(t):
                for c in ch: emoji_counter[c] += 1
        if URL_RE.search(t):   url_count += 1
        if PHONE_RE.search(t): phone_count += 1
        if "\n" in t or "\r" in t: nl_count += 1
        if MEDIA_PH.match(t):  media_ph += 1
        if l == 0:        buckets["0"] += 1
        elif l <= 50:     buckets["1-50"] += 1
        elif l <= 200:    buckets["51-200"] += 1
        elif l <= 500:    buckets["201-500"] += 1
        elif l <= 1000:   buckets["501-1000"] += 1
        elif l <= 5000:   buckets["1001-5000"] += 1
        else:             buckets[">5000"] += 1
    avg_len = round(sum(lengths)/len(lengths), 1) if lengths else 0.0
    return TextProfile(
        total_messages=total, empty_messages=empty, avg_length_chars=avg_len,
        max_length_chars=max(lengths) if lengths else 0,
        min_length_chars=min(lengths) if lengths else 0,
        messages_with_emoji=emoji_count, messages_with_urls=url_count,
        messages_with_phone_numbers=phone_count, messages_with_newlines=nl_count,
        messages_with_only_media_placeholder=media_ph,
        encoding_issues_count=enc_issues,
        top_emoji=[[e,c] for e,c in emoji_counter.most_common(10)],
        length_distribution=buckets,
    )
