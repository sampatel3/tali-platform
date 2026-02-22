"""Scoring constants: weights, fraud-detection patterns, and thresholds."""

MVP_WEIGHTS = {
    "tests_passed_ratio": 0.15,
    "time_efficiency": 0.05,
    "completion_time": 0.05,
    "clarity_score": 0.10,
    "context_score": 0.10,
    "specificity_score": 0.05,
    "independence_score": 0.10,
    "efficiency_score": 0.10,
    "iteration_score": 0.05,
    "response_utilization_score": 0.10,
    "decomposition_score": 0.05,
    "code_quality_score": 0.10,
}

VAGUE_PATTERNS = [
    r"^(help|fix|broken|not working|doesn't work|error|issue)\.?$",
    r"^(what's wrong|why isn't this working)\??$",
    r"^(please help|can you help|help me)\.?$",
    r"^(make|do|fix|build|write|implement|create|complete|finish)\s+(it|this|that|the|everything|all|the whole|the complete)\b",
    r"^(just|simply|please)\s+(make|do|fix|build|write|implement|finish)\b",
    r"^(write|create|implement|build)\s+(the\s+)?(whole|entire|complete|full)\b",
    r"^(make it work|get it working|make this work|make this pass|get the tests passing)\b",
    r"^(rewrite|redo|redo everything|start over)\b",
]

INJECTION_PATTERNS = [
    r"ignore (previous|all|prior) instructions",
    r"disregard (the )?(above|previous)",
    r"you are now",
    r"new instructions:",
    r"forget everything",
    r"(act|pretend|behave|think)\s+(as if|like|as though)\s+(you|you're|you are)",
    r"(no|without any|ignore all)\s+(restrictions|limits|constraints|rules)",
    r"(complete|write|implement|finish|solve)\s+(the|this|everything|all|entire|complete|whole)\s+(for me|for us|solution|code)",
    r"(give me|show me|write me)\s+(the|a)\s+(complete|full|entire|finished|working)"
    r"(?:\s+(complete|full|entire|finished|working))?\s+(solution|implementation|code|answer)",
]

# Copy-paste detection patterns (used in prompt_analytics)
COPYPASTE_PATTERNS = [
    r"(?i)^(here is|here's) (the|a|my) (solution|code|answer|implementation)",
    r"(?i)^(write|create|implement|build|make) (a |an |the )?(complete|full|entire)",
    r"(?i)stackoverflow\.com",
    r"(?i)chatgpt|openai|gemini|copilot",
    r"(?i)^```[\s\S]{200,}```$",  # Large code block as entire prompt
]

# Fraud score cap: if any fraud flag fires, final_score is capped to this value
FRAUD_SCORE_CAP = 50.0
