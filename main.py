from flask import Flask, jsonify
import json
import asyncio
import aiohttp
from datetime import datetime
import nest_asyncio

nest_asyncio.apply()
app = Flask(__name__)


MODEL_MAPPING = {
    "openai_gpt-3.5-turbo": {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-3.5-turbo",
        "key": OPENAI_API_KEY,
        "format": "messages",
    },
    "grok": {
        "url": "https://api.x.ai/v1/chat/completions",
        "model": "grok-3-latest",
        "key": GROK_API_KEY,
        "format": "messages",
    },
    "claude": {
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-3-5-sonnet-20241022",
        "key": CLAUDE_API_KEY,
        "format": "prompt",
    },
}

def load_all_humaneval_problems():
    with open("human-eval-v2-20210705.jsonl", "r") as f:
        return [json.loads(line) for line in f]

async def query_llm(model_name, prompt):
    model_info = MODEL_MAPPING[model_name]

    if model_info["format"] == "messages":
        payload = {
            "model": model_info["model"],
            "messages": [
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 500,
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {model_info['key']}",
            "Content-Type": "application/json",
        }
    elif model_name == "claude":
        payload = {
            "model": model_info["model"],
            "max_tokens": 500,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "x-api-key": model_info["key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        return {"error": f"Unsupported model format: {model_info['format']}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(model_info["url"], headers=headers, json=payload, timeout=60) as response:
                resp_text = await response.text()
                data = json.loads(resp_text)
                if model_name == "claude":
                    content = data.get("content", [])
                    return content[0].get("text", "No response") if isinstance(content, list) else content
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        return {"error": str(e)}

def extract_code(code_str):
    code_str = code_str.strip()
    if "List[" in code_str and "from typing import List" not in code_str:
        code_str = "from typing import List\n" + code_str
    return code_str

def test_candidate_solution(candidate_code, test_code):
    local_vars = {}
    try:
        exec(candidate_code, {}, local_vars)
        exec(test_code, local_vars, {})
        return {"passed": True}
    except Exception as e:
        return {"passed": False, "error": str(e)}

async def classify_concept_llm(prompt, reference_code):
    classification_prompt = (
        "You are a Python coding expert. Given the function and its description, "
        "classify the problem into one or more algorithmic concepts. Use the following labels: "
        "recursion, loop, conditional, string manipulation, sorting, searching, math, dynamic programming, greedy, edge case reasoning, unknown.\n\n"
        f"Description:\n{prompt}\n\nCode:\n{reference_code}\n\n"
        "Return only a comma-separated list of concepts with no explanation."
    )
    result = await query_llm("claude", classification_prompt)
    return [c.strip() for c in result.split(",")] if isinstance(result, str) else ["unknown"]

async def benchmark_all():
    problems = load_all_humaneval_problems()
    models = list(MODEL_MAPPING.keys())
    results_summary = {model: {} for model in models}
    concept_summary = {model: {} for model in models}
    concept_overall = {}
    failed_logs = []

    for i, problem in enumerate(problems):
        prompt = problem["prompt"]
        test_code = problem["test"]
        reference_code = problem["canonical_solution"]
        problem_id = problem["task_id"]
        code_generation_prompt = f"{prompt}\nWrite the complete function. Return only the code, no explanations or comments."

        concepts = await classify_concept_llm(prompt, reference_code)

        for concept in concepts:
            concept_overall.setdefault(concept, {"total": 0, "solved": 0})
            concept_overall[concept]["total"] += 1

        print(f"Benchmarking {problem_id} ({i + 1}/{len(problems)})")
        tasks = [query_llm(model, code_generation_prompt) for model in models]
        raw_outputs = await asyncio.gather(*tasks)

        problem_solved = False

        for model, raw in zip(models, raw_outputs):
            if isinstance(raw, dict) and "error" in raw:
                failed_logs.append({"problem_id": problem_id, "model": model, "error": raw["error"]})
                for concept in concepts:
                    concept_summary[model].setdefault(concept, {"passed": 0, "failed": 0})
                    concept_summary[model][concept]["failed"] += 1
                continue

            candidate_code = extract_code(raw)
            result = test_candidate_solution(candidate_code, test_code)
            if result["passed"]:
                problem_solved = True

            for concept in concepts:
                concept_summary[model].setdefault(concept, {"passed": 0, "failed": 0})
                concept_summary[model][concept]["passed" if result["passed"] else "failed"] += 1

            results_summary[model].setdefault("passed", 0)
            results_summary[model].setdefault("failed", 0)
            results_summary[model]["passed" if result["passed"] else "failed"] += 1

            if not result["passed"]:
                failed_logs.append({
                    "problem_id": problem_id,
                    "model": model,
                    "error": result["error"],
                    "code": candidate_code
                })

        if problem_solved:
            for concept in concepts:
                concept_overall[concept]["solved"] += 1

    for concept, stats in concept_overall.items():
        stats["accuracy"] = round((stats["solved"] / stats["total"]) * 100, 2) if stats["total"] > 0 else 0.0

    return results_summary, concept_summary, concept_overall, failed_logs

@app.route("/benchmark_conceptwise", methods=["GET"])
def flask_benchmark_concepts():
    loop = asyncio.get_event_loop()
    summary, concept_summary, concept_overall, logs = loop.run_until_complete(benchmark_all())

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(f"conceptwise_summary_{timestamp}.json", "w") as f:
        json.dump(concept_summary, f, indent=2)
    with open(f"conceptwise_overall_{timestamp}.json", "w") as f:
        json.dump(concept_overall, f, indent=2)
    with open(f"conceptwise_failed_logs_{timestamp}.json", "w") as f:
        json.dump(logs, f, indent=2)

    return jsonify({
        "message": "Benchmark with Claude-based concept classification completed",
        "summary": summary,
        "concept_accuracy_per_model": concept_summary,
        "concept_accuracy_overall": concept_overall,
        "log_file": f"conceptwise_failed_logs_{timestamp}.json"
    })
if __name__ == "__main__":
    app.run(debug=True)
