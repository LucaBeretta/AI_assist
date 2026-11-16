"""Small, validated performance summaries from Ollama response metadata."""

FIELDS = ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
          "eval_count", "eval_duration", "prompt_eval_cached_count")


def response_metrics(data):
    return {key: value for key in FIELDS if type(value := data.get(key)) in (int, float)
            and 0 <= value <= 2**63 - 1}


def metrics_summary(data):
    metrics = response_metrics(data)
    parts = []
    for key, label in (("total_duration", "Total"), ("load_duration", "Load"),
                       ("prompt_eval_duration", "Prompt")):
        if key in metrics:
            parts.append(f"{label} {metrics[key] / 1e9:.2f}s")
    if metrics.get("eval_duration", 0) > 0 and "eval_count" in metrics:
        parts.append(f"{metrics['eval_count'] / (metrics['eval_duration'] / 1e9):.1f} tokens/s")
    return " | ".join(parts)
