import os
import sys
import json
from openai import OpenAI
from vercel import get_all_project_infos

openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def run_agent(prompt: str, context: str = None) -> str:
    """
    Run the LLM agent with a user prompt and optional context (as a string, e.g. Vercel JSON).
    If no context is provided, automatically loads the latest Vercel project info.
    Returns the LLM's response as a string.
    """
    system = "You are an internal company assistant. Use the provided context to answer user questions."
    messages = [{"role": "system", "content": system}]
    if not context:
        vercel_projects = get_all_project_infos()
        context = json.dumps(vercel_projects, indent=2)
    messages.append({"role": "system", "content": f"Context:\n{context}"})
    messages.append({"role": "user", "content": prompt})
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=messages
    )
    return response.choices[0].message.content.strip()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run LLM agent with prompt and context.")
    parser.add_argument("prompt", type=str, help="User prompt/question for the agent.")
    parser.add_argument("--context_file", type=str, default=None, help="Path to JSON file with context (e.g. Vercel projects).")
    args = parser.parse_args()

    context = None
    if args.context_file:
        with open(args.context_file, "r", encoding="utf-8") as f:
            context = f.read()
    result = run_agent(args.prompt, context)
    print(result)

