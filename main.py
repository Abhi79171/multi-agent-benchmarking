import json
import asyncio
import aiohttp
import random
import ast
from datetime import datetime
from flask import Flask, jsonify, request
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
    "openai_gpt-4": {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4",
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
        "format": "messages",
    },
}

def load_all_mbpp_problems():
    with open("mbpp.jsonl", "r") as f:
        return [json.loads(line) for line in f]

def extract_signature_from_code(code):
    try:
        tree = ast.parse(code)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                func_name = node.name
                arg_names = [arg.arg for arg in node.args.args]
                return func_name, arg_names
    except Exception:
        return None, None
    return None, None

def build_prompt(problem_text, func_name, arg_names, test_cases):
    signature = f"def {func_name}({', '.join(arg_names)}):"
    examples = "\n".join(test_cases)
    prompt = (
        f"{problem_text}\n\n"
        f"Function signature to use:\n{signature}\n\n"
        f"Test cases to pass:\n{examples}\n\n"
        "Instructions:\n"
        "- Only implement the function body below the signature.\n"
        "- Use exactly the function name and arguments as given.\n"
        "- Do not print anything or use input().\n"
        "- Do not include explanations or markdown.\n"
        "- Return only Python code.\n"
    )
    return prompt

async def query_llm(model_name, prompt, max_retries=2):
    model_info = MODEL_MAPPING[model_name]

    payload = {
        "model": model_info["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "temperature": 0,
    }

    if model_name == "claude":
        headers = {
            "x-api-key": model_info["key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        headers = {
            "Authorization": f"Bearer {model_info['key']}",
            "Content-Type": "application/json",
        }

    for attempt in range(max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(model_info["url"], headers=headers, json=payload, timeout=60) as response:
                    resp_text = await response.text()
                    data = json.loads(resp_text)
                    if model_name == "claude":
                        content = data.get("content", [])
                        output = content[0].get("text", "No response") if content else "No response"
                    else:
                        output = data.get("choices", [{}])[0].get("message", {}).get("content", "No response")
                    return output
        except Exception as e:
            if attempt == max_retries:
                return {"error": f"{type(e).__name__}: {str(e)}"}
            print(f"[Retry {attempt + 1}] Exception for {model_name}: {e}")

    return {"error": "Max retries exceeded"}

def extract_code(code_str):
    if code_str.strip().startswith("```"):
        lines = code_str.splitlines()
        code_lines = [line for line in lines if not line.strip().startswith("```")]
        return "\n".join(code_lines).strip()
    return code_str.strip()

def test_candidate_solution(candidate_code, test_list, setup_code=""):
    local_vars = {}
    try:
        if setup_code.strip():
            exec(setup_code, {}, local_vars)
        exec(candidate_code, {}, local_vars)
        for idx, test_case in enumerate(test_list):
            try:
                exec(test_case, local_vars, {})
            except Exception as e:
                return {
                    "passed": False,
                    "error": f"Failed at test case {idx+1}: {test_case.strip()} | Error: {str(e)}",
                    "failed_test_case": test_case.strip()
                }
        return {"passed": True}
    except Exception as e:
        return {"passed": False, "error": f"Code execution error: {str(e)}"}

async def classify_concept_llm(problem_text, reference_code):
    classification_prompt = (
        "You are a Python coding expert. Given the problem description and reference solution, "
        "classify the problem into one or more algorithmic concepts. Use these labels: "
        "recursion, loop, conditional, string manipulation, sorting, searching, math, dynamic programming, greedy, edge case reasoning, unknown.\n\n"
        f"Problem Description:\n{problem_text}\n\nReference Solution:\n{reference_code}\n\n"
        "Return only a comma-separated list of concepts with no explanation."
    )
    result = await query_llm("claude", classification_prompt)
    if isinstance(result, dict) and "error" in result:
        return ["unknown"]
    return [c.strip() for c in result.split(",")] if isinstance(result, str) else ["unknown"]

async def benchmark_random_mbpp(n=None):
    problems = load_all_mbpp_problems()
    if n is None:
        n = len(problems)
    random_problems = random.sample(problems, min(n, len(problems)))
    models = list(MODEL_MAPPING.keys())
    results_summary = {model: {"passed": 0, "failed": 0} for model in models}
    concept_summary = {model: {} for model in models}
    concept_overall = {}
    failed_logs = []
    problems_solved_by_any_model = 0

    for i, problem in enumerate(random_problems):
        problem_text = problem.get("text", "")
        reference_code = problem.get("code", "")
        test_list = problem.get("test_list", [])
        test_setup_code = problem.get("test_setup_code", "")
        problem_id = problem.get("task_id", f"mbpp_{i}")
        func_name, arg_names = extract_signature_from_code(reference_code)

        if not func_name or not arg_names:
            continue

        concepts = await classify_concept_llm(problem_text, reference_code)
        for concept in concepts:
            concept_overall.setdefault(concept, {"total": 0, "solved": 0})
            concept_overall[concept]["total"] += 1

        prompt = build_prompt(problem_text, func_name, arg_names, test_list)

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Benchmarking {problem_id} ({i+1}/{len(random_problems)})")

        tasks = [query_llm(model, prompt) for model in models]
        raw_outputs = await asyncio.gather(*tasks)

        problem_solved = False

        for model, raw in zip(models, raw_outputs):
            if isinstance(raw, dict) and "error" in raw:
                results_summary[model]["failed"] += 1
                for concept in concepts:
                    concept_summary[model].setdefault(concept, {"passed": 0, "failed": 0})
                    concept_summary[model][concept]["failed"] += 1
                failed_logs.append({
                    "problem_id": problem_id,
                    "problem_text": problem_text,
                    "model": model,
                    "error": raw["error"],
                    "candidate_code": None
                })
                continue

            candidate_code = extract_code(raw)
            result = test_candidate_solution(candidate_code, test_list, setup_code=test_setup_code)

            if result["passed"]:
                results_summary[model]["passed"] += 1
                problem_solved = True
                for concept in concepts:
                    concept_summary[model].setdefault(concept, {"passed": 0, "failed": 0})
                    concept_summary[model][concept]["passed"] += 1
            else:
                results_summary[model]["failed"] += 1
                for concept in concepts:
                    concept_summary[model].setdefault(concept, {"passed": 0, "failed": 0})
                    concept_summary[model][concept]["failed"] += 1
                failed_logs.append({
                    "problem_id": problem_id,
                    "problem_text": problem_text,
                    "model": model,
                    "error": result["error"],
                    "candidate_code": candidate_code
                })

        if problem_solved:
            problems_solved_by_any_model += 1
            for concept in concepts:
                concept_overall[concept]["solved"] += 1

    for concept, stats in concept_overall.items():
        stats["accuracy"] = round((stats["solved"] / stats["total"]) * 100, 2) if stats["total"] > 0 else 0.0

    total_problems = len(random_problems)
    problems_solved_percentage = round((problems_solved_by_any_model / total_problems) * 100, 2) if total_problems > 0 else 0.0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    failed_log_file = f"failed_mbpp_logs_{timestamp}.json"
    with open(failed_log_file, "w") as f:
        json.dump(failed_logs, f, indent=2)

    return results_summary, concept_summary, concept_overall, failed_logs, problems_solved_by_any_model, problems_solved_percentage, total_problems, failed_log_file

@app.route("/benchmark_random_mbpp", methods=["GET"])
def flask_benchmark_random_mbpp():
    benchmark_all = request.args.get('benchmark_all', 'false').lower() == 'true'
    if benchmark_all:
        n = None
    else:
        n = request.args.get('n', default=10, type=int)
    loop = asyncio.get_event_loop()
    summary, concept_summary, concept_overall, logs, problems_solved, problems_solved_percentage, total_problems, failed_log_file = loop.run_until_complete(benchmark_random_mbpp(n=n))

    accuracy_summary = {}
    for model, res in summary.items():
        total = res["passed"] + res["failed"]
        accuracy_summary[model] = round((res["passed"] / total) * 100, 2) if total > 0 else 0.0

    return jsonify({
        "message": "Random MBPP benchmark with concept-wise tracking completed.",
        "summary": summary,
        "accuracy_percent": accuracy_summary,
        "concept_accuracy_per_model": concept_summary,
        "concept_accuracy_overall": concept_overall,
        "problems_solved_by_any_model": problems_solved,
        "total_problems_tested": total_problems,
        "problems_solved_percentage": problems_solved_percentage,
        "failed_cases_count": len(logs),
        "failed_log_file": failed_log_file
    })

if __name__ == "__main__":
    app.run(debug=True)