import json
import asyncio
import aiohttp
import re
import ast
from datetime import datetime
from flask import Flask, jsonify
import subprocess
import tempfile
import os

app = Flask(__name__)


MODEL_MAPPING = {
    "openai_gpt3.5": {
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
    }
}

def load_all_mbpp_problems():
    with open("mbpp.jsonl", "r") as f:
        return [json.loads(line) for line in f]

def extract_signature_from_code(code):
    try:
        tree = ast.parse(code)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                return node.name, [arg.arg for arg in node.args.args]
    except Exception:
        return None, None

def build_prompt(problem_text, func_name, arg_names, test_cases):
    signature = f"def {func_name}({', '.join(arg_names)}):"
    return (
        f"{problem_text}\n\n"
        f"Function signature to use:\n{signature}\n\n"
        f"Test cases to pass:\n{chr(10).join(test_cases)}\n\n"
        "Instructions:\n"
        "- Only implement the function body below the signature.\n"
        "- Use exactly the function name and arguments as given.\n"
        "- Do not print anything or use input().\n"
        "- Do not include explanations or markdown.\n"
        "- Return only Python code."
    )

async def query_llm(model_name, prompt, session, timeout=60):
    model_info = MODEL_MAPPING[model_name]
    headers = {}

    if model_info["format"] == "messages":
        payload = {
            "model": model_info["model"],
            "messages": [
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 600,
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {model_info['key']}",
            "Content-Type": "application/json",
        }
    elif model_name == "claude":
        payload = {
            "model": model_info["model"],
            "max_tokens": 600,
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
        async with session.post(model_info["url"], headers=headers, json=payload, timeout=timeout) as response:
            data = await response.json()
            if model_name == "claude":
                content = data.get("content", [])
                return content[0].get("text", "No response") if isinstance(content, list) else content
            return data.get("choices", [{}])[0].get("message", {}).get("content", "No response")
    except Exception as e:
        return {"error": f"{type(e)._name_}: {str(e)}"}

def extract_code(code_str):
    code_str = code_str.strip()
    if code_str.startswith(""):
        code_str = re.sub(r"^(python)?", "", code_str.strip(), flags=re.IGNORECASE)
        code_str = re.sub(r"```$", "", code_str.strip())
    if "List[" in code_str and "from typing import List" not in code_str:
        code_str = "from typing import List\n" + code_str
    return code_str.strip()

def test_candidate_solution(candidate_code, test_list, setup_code=""):
    # Write code to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        code = f"{setup_code}\n\n{candidate_code}\n\n"
        for i, test in enumerate(test_list):
            code += f"print('TEST_RESULT_{i}:', {test.split('assert ')[1]})\n"
        f.write(code)
        temp_file = f.name

    try:
        # Run the code in a subprocess with a timeout
        result = subprocess.run(
            ["python", temp_file],
            capture_output=True,
            text=True,
            timeout=5  # 5-second timeout per problem
        )
        output = result.stdout.splitlines()
        for i, line in enumerate(output):
            if f"TEST_RESULT_{i}:" not in line:
                continue
            if "False" in line:
                return {"passed": False, "error": f"Test {i+1} failed"}
        if result.stderr:
            return {"passed": False, "error": f"Execution error: {result.stderr}"}
        return {"passed": True}
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "Execution timed out"}
    except Exception as e:
        return {"passed": False, "error": f"Execution failed: {str(e)}"}
    finally:
        os.unlink(temp_file)  # Clean up temporary file

async def safe_query(model, prompt, session, max_retries=3):
    for attempt in range(max_retries):
        try:
            async with asyncio.Semaphore(3):
                return await asyncio.wait_for(query_llm(model, prompt, session), timeout=90)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:  # Rate limit error
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                print(f"Rate limit hit for {model}, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
                continue
            return {"error": f"{type(e)._name_}: {str(e)}"}

async def benchmark_all_mbpp():
    problems = load_all_mbpp_problems()
    models = list(MODEL_MAPPING.keys())
    summary = {m: {"passed": 0, "failed": 0} for m in models}
    failed_logs = []
    solved_by_any = 0

    async with aiohttp.ClientSession() as session:
        for i, problem in enumerate(problems):
            print(f"Benchmarking problem {problem.get('task_id', i)} ({i+1}/{len(problems)}) at {datetime.now().strftime('%H:%M:%S')}")
            fname, args = extract_signature_from_code(problem.get("code", ""))
            if not fname or not args:
                print(f"Skipping problem {i} due to invalid function signature")
                continue
            prompt = build_prompt(problem["text"], fname, args, problem["test_list"])
            print(f"Starting API queries for problem {i}")
            tasks = [safe_query(model, prompt, session) for model in models]
            outputs = await asyncio.gather(*tasks, return_exceptions=True)
            print(f"Finished API queries for problem {i}")

            solved = False
            for model, raw in zip(models, outputs):
                if isinstance(raw, Exception) or (isinstance(raw, dict) and "error" in raw):
                    summary[model]["failed"] += 1
                    failed_logs.append({"problem_id": i, "model": model, "error": str(raw)})
                    print(f"API error for {model} on problem {i}: {str(raw)}")
                    continue
                code = extract_code(raw)
                print(f"Testing solution for {model} on problem {i}")
                result = test_candidate_solution(code, problem["test_list"], problem.get("test_setup_code", ""))
                print(f"Finished testing for {model} on problem {i}")
                if result["passed"]:
                    summary[model]["passed"] += 1
                    solved = True
                else:
                    summary[model]["failed"] += 1
                    failed_logs.append({"problem_id": i, "model": model, "error": result["error"], "code": code})

            if solved:
                solved_by_any += 1
            await asyncio.sleep(5)  # Delay to avoid rate limits

    percent = round((solved_by_any / len(problems)) * 100, 2)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"failed_mbpp_logs_{timestamp}.json"
    with open(log_file, "w") as f:
        json.dump(failed_logs, f, indent=2)

    return summary, failed_logs, solved_by_any, percent, len(problems), log_file

@app.route("/benchmark_all_mbpp", methods=["GET"])
def run_benchmark():
    summary, logs, solved, percent, total, logfile = asyncio.run(benchmark_all_mbpp())
    accuracy = {
        m: round((res["passed"] / (res["passed"] + res["failed"])) * 100, 2) if (res["passed"] + res["failed"]) else 0
        for m, res in summary.items()
    }
    return jsonify({
        "message": "Benchmark completed",
        "summary": summary,
        "accuracy_percent": accuracy,
        "solved": solved,
        "total": total,
        "solved_percent": percent,
        "failed_cases": len(logs),
        "log_file": logfile
    })

if __name__ == "__main__":
    app.run(debug=True)