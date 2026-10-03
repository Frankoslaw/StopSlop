import argparse
import sys
import httpx
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError
from stopslop.config import Settings, POLICIES
from stopslop.router import PolicyRouter


def chat(client, model, messages):
    # Normal NVIDIA/OpenAI example; enforcement is injected at construction.
    options = {"reasoning_budget": 0} if model == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning" else {}
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        top_p=1,
        max_tokens=256,
        stream=False,
        extra_body=options,
    )
    message = completion.choices[0].message
    # Some hosted responses put a thinking delimiter inside content. Show the final answer.
    answer = (message.content or "").rsplit("</think>", 1)[-1].strip()
    print(answer or "[Model returned no text]", flush=True)
    return answer


def main():
    parser = argparse.ArgumentParser(description="NVIDIA chat with an in-process StopSlop router")
    parser.add_argument("prompt", nargs="?", help="Single prompt; omit for interactive chat")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--policy", choices=POLICIES)
    parser.add_argument("--model", dest="main_model")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()
    try:
        settings = Settings.load(args.env_file, policy=args.policy, main_model=args.main_model, timeout=args.timeout)
        if not settings.main_key:
            parser.error("Set STOPSLOP_MAIN_KEY in .env or the environment")
        router = PolicyRouter(settings)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    # This injection attaches policy enforcement without a server or local port.
    with OpenAI(base_url=settings.main_base_url, api_key=settings.main_key,
                http_client=httpx.Client(transport=router, timeout=settings.timeout),
                timeout=settings.timeout, max_retries=0) as client:
        if args.prompt is None:
            print("NVIDIA chat. Type /quit to exit. Each accepted turn makes one model call.")
        while True:
            try:
                prompt = args.prompt if args.prompt is not None else input("You: ")
            except (EOFError, KeyboardInterrupt):
                return
            if args.prompt is None and prompt.strip() == "/quit":
                return
            if not prompt.strip():
                if args.prompt is not None:
                    parser.error("prompt must not be empty")
                continue
            candidate = messages + [{"role": "user", "content": prompt}]
            try:
                answer = chat(client, settings.main_model, candidate)
                messages = candidate + [{"role": "assistant", "content": answer or ""}]
            except APIStatusError as error:
                code = error.body.get("code", "upstream_error") if isinstance(error.body, dict) else "upstream_error"
                print(f"Rejected: HTTP {error.status_code} ({code})", file=sys.stderr)
                if args.prompt is not None or error.status_code == 429:
                    raise SystemExit(1)
            except APITimeoutError:
                print("NVIDIA request timed out; no retry was made. Try --timeout 120.", file=sys.stderr)
                raise SystemExit(1)
            except APIConnectionError as error:
                print(f"Cannot connect to NVIDIA ({type(error.__cause__).__name__}); no retry was made.", file=sys.stderr)
                raise SystemExit(1)
            if args.prompt is not None:
                return
