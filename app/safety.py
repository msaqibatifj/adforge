"""Deterministic safety layer (free, no tokens). The LLM classifier in graph.py handles the fuzzy cases."""
import re

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|system)?\s*(instructions?|prompts?|rules?|guidelines?)",
    r"disregard\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|system)?\s*(instructions?|prompts?|rules?)",
    r"forget\s+(all\s+|everything\s+|your\s+)(previous\s+)?(instructions?|rules?|you were told)",
    r"(reveal|show|print|repeat|display|leak|output|tell me)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions?|rules|configuration|api[\s_-]?key|secret|credentials?|env)",
    r"what\s+(is|are|were)\s+your\s+(system\s+)?(prompt|instructions)",
    r"you\s+are\s+now\s+(a|an|in|DAN)\b",
    r"\b(developer|debug|admin|god|jailbreak)\s+mode\b",
    r"\bDAN\b.*\b(mode|jailbreak|prompt)\b",
    r"(override|bypass|disable|turn\s+off)\s+(your\s+|the\s+|all\s+)?(safety|security|restrictions?|guardrails?|filters?|rules?|confirmation)",
    r"pretend\s+(you\s+)?(have\s+no|don'?t\s+have|there\s+are\s+no)\s+(rules|restrictions|limits)",
    r"(<\|im_start\|>|<\|system\|>|\[\s*system\s*\]|###\s*system|</?system>)",
    r"\b(anthropic|admin|system|developer)\s+(says|message|override|instruction)s?\s*:",
    # cross-user access attempts
    r"(another|other|different|someone\s+else'?s?|all)\s+(users?|customers?|accounts?|clients?)('s|s')?\s+(data|campaigns?|memor(y|ies)|information|history|results|ads?)",
    r"(data|campaigns?|memor(y|ies)|history|results|ads?)\s+(of|from|belonging\s+to|for)\s+(another|other|different|someone\s+else|all)\s+(users?|customers?|accounts?|clients?)",
    r"(of|for|from)\s+(user|account)[\s_:-]+\w*[\d_]\w*",
    r"\buser[_\s-]?id\s*(=|:|is|to)\s*['\"]?\w+",
    r"(switch|act|impersonate|log\s*in)\s+(as|to|into)\s+(user|another|the\s+admin|admin)",
    r"(list|dump|show|export)\s+(all\s+)?(the\s+)?(users|database|db|tables|memories)\b",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

AD_KEYWORDS = re.compile(
    r"\b(ad|ads|advert\w*|campaign\w*|headline\w*|copy|copywriting|a/?b|variant\w*|ctr|cvr|conversion\w*|impression\w*|click\w*|"
    r"creative\w*|banner\w*|cta|launch|brand\w*|audience\w*|target\w*|budget|facebook|meta|instagram|google|tiktok|linkedin|"
    r"youtube|promo\w*|marketing|slogan\w*|tagline\w*|platform|results?|winner|analy\w+|product|tone|remember|memory)\b", re.I)

REFUSAL_OFF_TOPIC = ("I'm AdForge, an AI ad agency assistant, so I can only help with advertising: ad copy, creatives, campaigns, "
                     "A/B tests and results. Tell me about a product you'd like to advertise and I'll get started.")
REFUSAL_INJECTION = ("I can't do that. I won't change my rules, reveal my instructions or credentials, or access anyone else's data. "
                     "I'm happy to keep working on your own ad campaigns, though.")
BUDGET_MESSAGE = "AdForge is temporarily at capacity. Please try again later."


def is_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJ)


def looks_on_topic(text: str, has_history: bool) -> bool:
    """Cheap pre-check so most turns skip the LLM classifier."""
    if AD_KEYWORDS.search(text):
        return True
    return has_history and len(text.split()) <= 6   # short follow-ups: "yes", "make it shorter", "B please"


def clean_fact(key: str, value: str):
    key = re.sub(r"[^a-z0-9_]+", "_", str(key).lower()).strip("_")[:40]
    value = re.sub(r"\s+", " ", str(value)).strip()[:200]
    if not key or not value or is_injection(value):
        return None
    return key, value
