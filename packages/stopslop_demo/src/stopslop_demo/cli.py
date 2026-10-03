import argparse
import sys
from pathlib import Path
from dataclasses import replace
import httpx
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError
from stopslop.config import Settings, POLICIES
from stopslop.router import PolicyRouter
from .logs import configure_logs, configure_console
from .spinner import Spinner, set_status
from .scenarios import SCENARIOS
import logging
import time


def chat(client, model, messages, expected_action=None, max_tokens=96, retries=0, prepare=None):
    # Normal NVIDIA/OpenAI example; enforcement is injected at construction.
    options = {"reasoning_budget": 0} if model == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning" else {}
    if prepare is not None:
        prepare()
    with Spinner():
        for attempt in range(retries + 1):
            try:
                raw = client.chat.completions.with_raw_response.create(
                    model=model, messages=messages, temperature=0.2, top_p=1,
                    max_tokens=max_tokens, stream=False, extra_body=options,
                )
                break
            except APIStatusError as error:
                code = error.body.get("code") if isinstance(error.body, dict) else None
                if error.status_code not in (502, 503, 504) or code != "upstream_error" or attempt == retries:
                    raise
                delay = 2 * (attempt + 1)
                logging.getLogger("stopslop.audit").warning(
                    "Chat backend HTTP %s; retry %s/%s in %ss", error.status_code, attempt + 1, retries, delay)
                set_status(f"Waiting {delay}s before chat retry {attempt + 1}/{retries}")
                time.sleep(delay)
    if expected_action is not None:
        action = raw.headers.get("X-StopSlop-Action", "unknown")
        if action != expected_action:
            logging.getLogger("stopslop.audit").error("Scenario expected action=%s, received=%s", expected_action, action)
            raise SystemExit(1)
    completion = raw.parse()
    message = completion.choices[0].message
    # Some hosted responses put a thinking delimiter inside content. Show the final answer.
    answer = (message.content or "").rsplit("</think>", 1)[-1].strip()
    print(answer or "[Model returned no text]", flush=True)
    return answer


def main():
    configure_console()
    parser = argparse.ArgumentParser(description="OpenAI-compatible chat with an in-process StopSlop router")
    parser.add_argument("prompt", nargs="?", help="Single prompt; omit for interactive chat")
    parser.add_argument("--scenario", nargs="?", const="nda", choices=SCENARIOS, help="Run scripted turns in order (default: nda)")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--policy", choices=POLICIES)
    parser.add_argument("--classifier", dest="classifier_override", choices=("laya", "jev", "llm"), help="Override policy and budget fallback classifier")
    parser.add_argument("--main-provider", choices=("nvidia", "openai", "ollama"))
    parser.add_argument("--main-base-url")
    parser.add_argument("--model", dest="main_model")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--max-tokens", type=int, default=96, help="Maximum reply tokens (default: 96)")
    parser.add_argument("--retries", type=int, default=2, help="Scenario retries for chat HTTP 502/503/504 only (default: 2)")
    parser.add_argument("--policy-file")
    parser.add_argument("--deterministic", action="store_true", default=None, help="Skip all semantic evaluators; use regex rules only")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--local-model")
    parser.add_argument("--local-base-url")
    parser.add_argument("--dynamic-policy-file")
    parser.add_argument("--log-chats", action=argparse.BooleanOptionalAction, default=None, help="Record chats in SQLite (default: enabled; disable with --no-log-chats)")
    parser.add_argument("--state-file")
    args = parser.parse_args()
    if args.max_tokens <= 0:
        parser.error("--max-tokens must be positive")
    if not 0 <= args.retries <= 5:
        parser.error("--retries must be between 0 and 5")
    if args.scenario and args.prompt is not None:
        parser.error("Use --scenario or a single prompt, not both")
    logger = configure_logs(args.color)
    try:
        settings = Settings.load(args.env_file, policy=args.policy, main_provider=args.main_provider,
                                 main_base_url=args.main_base_url, main_model=args.main_model, timeout=args.timeout,
                                 classifier_override=args.classifier_override,
                                 policy_file=args.policy_file, local_model=args.local_model, local_base_url=args.local_base_url,
                                 deterministic=args.deterministic, dynamic_policy_file=args.dynamic_policy_file,
                                 log_chats=args.log_chats, state_file=args.state_file)
        if not settings.policy_file and not settings.rules_file and args.policy is None and Path("policy.toml").is_file():
            settings = replace(settings, policy_file="policy.toml")
        if not settings.main_key:
            parser.error("Set STOPSLOP_MAIN_KEY in .env or the environment")
        router = PolicyRouter(settings)
        if router.policy.injected_evaluator is None:
            from stopslop.preflight import check_semantic_setup
            check_semantic_setup(settings)
        router.policy.prepare_models()
        logger.info("Policy file=%s evaluation backend=%s", settings.policy_file or "global",
                    "deterministic only" if settings.deterministic else router.policy.evaluator.name + " (" + router.policy.evaluator.model + ")")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    messages = [{"role": "system", "content": "You are a helpful assistant. Give concise plain-text replies, at most three short bullet points. Avoid tables and repeating prior answers."}]
    scenario = iter(SCENARIOS[args.scenario]) if args.scenario else None
    # This injection attaches policy enforcement without a server or local port.
    with OpenAI(base_url=settings.main_base_url, api_key=settings.client_token or settings.main_key,
                http_client=httpx.Client(transport=router, timeout=settings.timeout),
                timeout=settings.timeout, max_retries=0) as client:
        if args.prompt is None and scenario is None:
            print(f"{settings.main_provider.upper()} chat. Type /quit to exit. Each turn is checked by the configured policies before the chat model runs.")
        if scenario is not None:
            print("Scenario: " + args.scenario + " (invented demonstration data)", flush=True)
        turn_number = 0
        while True:
            turn = None
            try:
                if scenario is not None:
                    turn = next(scenario, None)
                    if turn is None:
                        logger.info("Scenario complete: all expected actions verified")
                        return
                    turn_number += 1
                    print(f"\nYou [{turn_number}, expected={turn.expected_action}]: {turn.prompt}", flush=True)
                    prompt = turn.prompt
                else:
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
                answer = chat(client, settings.main_model, candidate, expected_action=turn.expected_action,
                              max_tokens=args.max_tokens, retries=args.retries, prepare=router.policy.prepare_models) if turn else chat(client, settings.main_model, candidate, max_tokens=args.max_tokens, prepare=router.policy.prepare_models)
                messages = candidate + [{"role": "assistant", "content": answer or ""}]
            except KeyboardInterrupt:
                logger.warning("Cancelled while waiting for the backend; no retry was made.")
                raise SystemExit(130) from None
            except APIStatusError as error:
                code = error.body.get("code", "upstream_error") if isinstance(error.body, dict) else "upstream_error"
                if code != "policy_blocked":
                    logger.error("Rejected: HTTP %s (%s)", error.status_code, code)
                if turn is not None:
                    if code == "policy_blocked" and turn.expected_action == "block":
                        logger.info("Scenario NDA restriction verified: blocked before chat generation")
                        continue
                    logger.error("Scenario failed: expected=%s", turn.expected_action)
                    raise SystemExit(1)
                if args.prompt is not None or error.status_code == 429:
                    raise SystemExit(1)
            except APITimeoutError:
                logger.error("Backend timed out after %.0fs; no retry was made. Increase --timeout or try another --model.", settings.timeout)
                raise SystemExit(1)
            except APIConnectionError as error:
                logger.error("Cannot connect to chat backend (%s); no retry was made.", type(error.__cause__).__name__)
                raise SystemExit(1)
            if args.prompt is not None:
                return
