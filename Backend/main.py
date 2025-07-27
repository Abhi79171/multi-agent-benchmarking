from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import ast
import asyncio
import aiohttp
from openai import AsyncOpenAI
import anthropic
import sqlite3
import hashlib


app = Flask(__name__)
CORS(app)

from dotenv import load_dotenv
load_dotenv()



openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
claude_client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)

async def query_openai(prompt):
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"# Error: {str(e)}"

async def query_claude(prompt):
    try:
        response = await claude_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2048,
            system="You are a Python expert. Only return valid Python code.",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        return f"# Error: {str(e)}"

async def query_grok(prompt):
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "grok-3-latest",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post("https://api.x.ai/v1/chat/completions", headers=headers, json=payload) as resp:
                result = await resp.json()
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            return f"# Error: {str(e)}"

def extract_code(text):
    match = re.search(r"```(?:python)?(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()

def estimate_complexity(code):
    time, space = "O(n)", "O(1)"
    if code.count("for") >= 2: time = "O(n^2)"
    if "dict(" in code or "set(" in code: space = "O(n)"
    return time, space



def extract_test_cases(problem_description):
    examples = re.findall(r'Example \d+:(.*?)(?=Example \d+:|\Z)', problem_description, re.DOTALL)
    test_cases = []
    for ex in examples:
        input_match = re.search(r'Input:\s*(.*)', ex)
        output_match = re.search(r'Output:\s*(.*)', ex)
        if input_match and output_match:
            input_str = input_match.group(1).strip()
            output_str = output_match.group(1).strip()
            try:
                args = parse_input(input_str)
                expected_str = output_str.replace("true", "True").replace("false", "False")
                expected = eval(expected_str)
                test_cases.append({'input': args, 'expected': expected})
            except Exception as e:
                print(f"Test case parse error: {e}")
                continue
    return test_cases

def parse_input(input_line):
    parts = re.split(r',(?![^\[\]{}]*[\]\}])', input_line)
    args = []
    for part in parts:
        if '=' not in part:
            raise ValueError(f"Invalid input format: {part}")
        _, val = part.split('=', 1)
        args.append(eval(val.strip()))
    if len(args) == 1:
        return args[0]
    return tuple(args)

def run_tests(code_str, func_name, tests):
    try:
        exec_globals = {}
        exec(code_str, exec_globals)
        fn = next((val for val in exec_globals.values() if callable(val)), None)
        if not fn:
            return False, "No function found"
        for test in tests:
            args = test["input"]
            if not isinstance(args, tuple):
                args = (args,)
            result = fn(*args)
            if result != test["expected"]:
                return False, f"Failed for input: {test['input']} | Got: {result} | Expected: {test['expected']}"
        return True, "All tests passed"
    except Exception as e:
        return False, str(e)

async def refine_model(model_name, original_prompt, test_cases, old_code, error_msg):
    refinement_prompt = f"""
You previously attempted this problem but your solution failed the tests.

Problem:
{original_prompt}

Your Failed Code:
{old_code}

Failure Message:
{error_msg}

Test Cases:
{"".join([f"Input: {t['input']}, Expected Output: {t['expected']}" for t in test_cases])}

Please fix the code. Do NOT explain anything. Just return correct Python code (no markdown).
"""
    if model_name == "gpt-4o":
        return await query_openai(refinement_prompt)
    elif model_name == "claude-3.5":
        return await query_claude(refinement_prompt)
    elif model_name == "grok-3":
        return await query_grok(refinement_prompt)
    return "# Error: Unknown model"

@app.route("/solve", methods=["POST"])
def solve():
    data = request.json
    problem = data.get("description")
    function_name = data.get("function_name", "solution")
    test_cases = extract_test_cases(problem)

    if not test_cases:
        return jsonify({"error": "No valid test cases found."}), 400

    prompt = f"""You are a Python coding assistant.

Write a clean and correct Python function for the following problem.

Problem:
{problem}

Constraints:
- Only return code (no explanation, no markdown).
- Use the exact function name: `{function_name}`
- Do not use type annotations like list[int] or -> str.
- Use Python 3.6+ compatible syntax.
Only return pure executable Python code.
"""

    async def process_all_models():
        models = ["gpt-4o", "claude-3.5", "grok-3"]
        query_funcs = [query_openai, query_claude, query_grok]
        results = []

        raw_responses = await asyncio.gather(*[func(prompt) for func in query_funcs])

        for model, response in zip(models, raw_responses):
            code = extract_code(response)
            time_cx, space_cx = estimate_complexity(code)
            passed, feedback = run_tests(code, function_name, test_cases)
            results.append({
                "model": model,
                "code": code,
                "passes_tests": passed,
                "test_feedback": feedback,
                "time_complexity": time_cx,
                "space_complexity": space_cx
            })

        if not any(r["passes_tests"] for r in results):
            for _ in range(5):
                for i, r in enumerate(results):
                    if not r["passes_tests"]:
                        refined = await refine_model(
                            r["model"], problem, test_cases, r["code"], r["test_feedback"]
                        )
                        code = extract_code(refined)
                        time_cx, space_cx = estimate_complexity(code)
                        passed, feedback = run_tests(code, function_name, test_cases)
                        r.update({
                            "code": code,
                            "passes_tests": passed,
                            "test_feedback": feedback,
                            "time_complexity": time_cx,
                            "space_complexity": space_cx
                        })
                if any(r["passes_tests"] for r in results):
                    break

        passed = [r for r in results if r["passes_tests"]]
        best = sorted(passed, key=lambda x: (x["time_complexity"], x["space_complexity"]))[0] if passed else {
            "model": None, "reason": "No model passed after 5 refinements"
        }

        return jsonify({
            "problem_id": data.get("problem_id"),
            "solutions": results,
            "best_model": best.get("model"),
            "best_solution": best
        })

    return asyncio.run(process_all_models())
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()
def create_user_table():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    confirm_password = data.get("confirm_password")

    if not all([first_name, last_name, username, email, password, confirm_password]):
        return jsonify({"error": "All fields are required."}), 400
    if password != confirm_password:
        return jsonify({"error": "Passwords do not match."}), 400

    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("INSERT INTO users (first_name, last_name, username, email, password) VALUES (?, ?, ?, ?, ?)",
                  (first_name, last_name, username, email, hash_password(password)))
        conn.commit()
        return jsonify({"message": "User registered successfully."}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists."}), 409
    finally:
        conn.close()

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()

    if row and row[0] == hash_password(password):
        return jsonify({"message": "Login successful."})
    return jsonify({"error": "Invalid username or password."}), 401

if __name__ == "__main__":
    create_user_table()
    app.run(host="0.0.0.0", port=5000, debug=True)
