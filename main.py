from flask import Flask, jsonify
import json
import asyncio
import aiohttp
from datetime import datetime
import nest_asyncio

nest_asyncio.apply()
app = Flask(__name__)

MODEL_MAPPING = {
    "openai_gpt4": {
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
                print(f"{model_name} response:\n{resp_text}\n")
                data = json.loads(resp_text)
                if model_name == "claude":
                    content = data.get("content", [])
                    return content[0].get("text", "No response") if isinstance(content, list) else content
                try:
                    return data["choices"][0]["message"]["content"]
                except (KeyError, IndexError):
                    return f"Unexpected format: {json.dumps(data)}"
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


async def benchmark_all():
    problems = load_all_humaneval_problems()
    models = list(MODEL_MAPPING.keys())
    results_summary = {model: {"passed": 0, "failed": 0} for model in models}
    failed_logs = []
    problems_solved_by_any_model = 0

    for i, problem in enumerate(problems):
        prompt = problem["prompt"]
        test_code = problem["test"]
        problem_id = problem["task_id"]
        code_generation_prompt = f"{prompt}\nWrite the complete function. Return only the code, no explanations or comments."

        print(f"Benchmarking {problem_id} ({i + 1}/{len(problems)})")
        tasks = [query_llm(model, code_generation_prompt) for model in models]
        raw_outputs = await asyncio.gather(*tasks)

        problem_solved = False

        for model, raw in zip(models, raw_outputs):
            if isinstance(raw, dict) and "error" in raw:
                results_summary[model]["failed"] += 1
                failed_logs.append({
                    "problem_id": problem_id,
                    "model": model,
                    "error": raw["error"]
                })
                continue

            candidate_code = extract_code(raw)
            result = test_candidate_solution(candidate_code, test_code)
            if result["passed"]:
                results_summary[model]["passed"] += 1
                problem_solved = True
            else:
                results_summary[model]["failed"] += 1
                failed_logs.append({
                    "problem_id": problem_id,
                    "model": model,
                    "error": result["error"],
                    "code": candidate_code
                })

        if problem_solved:
            problems_solved_by_any_model += 1

    total_problems = len(problems)
    problems_solved_percentage = round((problems_solved_by_any_model / total_problems) * 100, 2)

    return results_summary, failed_logs, problems_solved_by_any_model, problems_solved_percentage, total_problems


@app.route("/benchmark_all_humaneval", methods=["GET"])
def flask_benchmark():
    loop = asyncio.get_event_loop()
    summary, logs, problems_solved, problems_solved_percentage, total_problems = loop.run_until_complete(
        benchmark_all())

    accuracy_summary = {}
    for model, res in summary.items():
        total = res["passed"] + res["failed"]
        accuracy_summary[model] = round((res["passed"] / total) * 100, 2) if total > 0 else 0.0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    failed_log_file = f"humaneval_failed_logs_{timestamp}.json"
    summary_file = f"humaneval_summary_{timestamp}.json"

    with open(failed_log_file, "w") as f:
        json.dump(logs, f, indent=2)

    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    return jsonify({
        "message": "Full HumanEval benchmark completed.",
        "summary": summary,
        "accuracy_percent": accuracy_summary,
        "problems_solved_by_any_model": problems_solved,
        "total_problems": total_problems,
        "problems_solved_percentage": problems_solved_percentage,
        "failed_log_file": failed_log_file
    })


if __name__ == "__main__":
    app.run(debug=True)