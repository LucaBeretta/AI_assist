"""One-off live check for the Hugging Face suggestions (temporary branch only, not for main).

Reads Hugging Face's real model list with the new code, shows what would be
suggested for a few computers, and prints the smallest downloadable tag so the
workflow can ask a real Ollama to pull it.
"""
import json
import os
import sys

import model_advisor
import model_library

GIB = model_advisor.GIB

models = model_library.fetch_hugging_face_models()
print(f"Read {len(models)} Hugging Face models with downloadable GGUF files:")
for model in models:
    quants = ", ".join(f"{v['quant']} {v['size_bytes'] / GIB:.1f} GB" for v in model["variants"])
    print(f"  {model['repo']}  params={model['parameters']}  downloads={model['downloads']}\n      {quants}")
assert models, "no models"
assert all(v["name"].startswith("hf.co/") for m in models for v in m["variants"])

computers = {
    "PC with RTX 4090 (24 GB)": {
        "ram_total_bytes": 64 * GIB, "ram_available_bytes": 48 * GIB, "disk_free_bytes": 500 * GIB, "server_local": True,
        "gpus": model_advisor._parse_nvidia("NVIDIA GeForce RTX 4090, 24564, 22000, 610.88, 8.9")},
    "Laptop, no GPU, 16 GB": {
        "ram_total_bytes": 16 * GIB, "ram_available_bytes": 10 * GIB, "disk_free_bytes": 200 * GIB,
        "server_local": True, "gpus": []},
}
for label, hardware in computers.items():
    cards = model_advisor.recommend_hugging_face(hardware, models)
    print(f"\nSuggestions for {label}:")
    for card in cards:
        print(f"  {card['name']}  {card['size_bytes'] / GIB:.1f} GB  [{card['fit']}]  {card['purpose']}")

smallest = min((v for m in models for v in m["variants"]), key=lambda v: v["size_bytes"])
print(f"\nSmallest tag: {smallest['name']} ({smallest['size_bytes'] / GIB:.2f} GB)")
with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as output:
    output.write(f"tag={smallest['name'] if smallest['size_bytes'] < 2 * GIB else 'hf.co/bartowski/SmolLM2-135M-Instruct-GGUF:Q4_K_M'}\n")
sys.stdout.flush()
json.dumps(models)  # the API result must stay plain JSON for the web UI
