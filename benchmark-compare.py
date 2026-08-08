#!/usr/bin/env python3
"""Compare qwen-flash-character vs qwen-plus-character — speed + response quality."""
import json, time, urllib.request, urllib.error

BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

acc = json.load(open(r"D:\qwencloud-generator\accounts.json", encoding="utf-8"))
keys = []
for k, v in acc.items():
    if k.startswith("_") or v.get("status") != "success" or not v.get("api_key"):
        continue
    keys.append((k, v["api_key"]))
email, key = keys[0]
print(f"Key: {email}\n")

def chat(model, messages, max_tokens=200, timeout=60):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=data, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8", errors="replace"))
            dt = time.time() - t0
            msg = resp.get("choices", [{}])[0].get("message", {})
            usage = resp.get("usage", {})
            return {
                "dt": dt,
                "content": msg.get("content", ""),
                "ttft": resp.get("choices", [{}])[0].get("timing", {}),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
    except urllib.error.HTTPError as e:
        return {"error": f"{e.code} {e.read().decode('utf-8','replace')[:200]}"}
    except Exception as e:
        return {"error": str(e)}

# 5 benchmark tasks: reasoning, coding, indonesian, math, long context
TASKS = [
    ("reasoning", [{"role": "user", "content": "Solve step by step: if x + 2y = 10 and 3x - y = 5, what is x and y?"}]),
    ("coding", [{"role": "user", "content": "Write a Python function to check if a string is a palindrome."}]),
    ("indonesian", [{"role": "user", "content": "Jelaskan dalam bahasa Indonesia apa itu machine learning dalam 3 kalimat."}]),
    ("math", [{"role": "user", "content": "What is 17 * 23 - 5^3? Answer with calculation."}]),
    ("long_context", [{"role": "user", "content": "Summarize in one sentence: " + "The quick brown fox jumps over the lazy dog. " * 30}]),
]

for name, msgs in TASKS:
    print(f"--- {name} ---")
    for model in ["qwen-flash-character", "qwen-plus-character"]:
        r = chat(model, msgs)
        if "error" in r:
            print(f"  {model:25s} ERROR: {r['error'][:80]}")
        else:
            print(f"  {model:25s} {r['dt']:5.2f}s tok={r['total_tokens']:4d} resp={r['content'][:60]!r}")
    print()

# Speed test: 3x same task for latency comparison
print("--- latency (3 runs, same task) ---")
for model in ["qwen-flash-character", "qwen-plus-character"]:
    times = []
    for i in range(3):
        r = chat(model, [{"role": "user", "content": "Say hello"}], max_tokens=10)
        if "error" not in r:
            times.append(r["dt"])
    if times:
        print(f"  {model:25s} avg={sum(times)/len(times):.2f}s min={min(times):.2f}s max={max(times):.2f}s")
    else:
        print(f"  {model:25s} all failed")
