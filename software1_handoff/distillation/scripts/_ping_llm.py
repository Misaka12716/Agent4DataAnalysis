import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from distillation.software1_pipeline_demo_app import llm_client

cfg = llm_client.get_config()
print("model    =", cfg.model)
print("base_url =", cfg.base_url)
print("api_key  =", cfg.api_key[:8] + "..." + cfg.api_key[-4:])

out = llm_client.chat_json(
    system="You are a JSON-only oracle. Reply with one JSON object, no prose.",
    user='Reply with this exact JSON object: {"hello": "world", "sum_of_2_and_3": 5}',
    max_tokens=120, temperature=0.0,
)
print("ping     =", out)
