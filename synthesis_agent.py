import json
import time
import threading
from dotenv import load_dotenv
import anthropic

load_dotenv()
client = anthropic.Anthropic()

ANALYSTS = {
    "defensive": "You are a defensive tactics analyst for a soccer coach.",
    "offensive": "You are an attacking tactics analyst for a soccer coach.",
    "physical": "You are a fitness and substitution analyst for a soccer coach."
}

results = {}
lock = threading.Lock()

def call_model(prompt, max_tokens=1024):
    r = client.messages.create(
        model="Qwen3.5-397B-A17B",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": f"Reply with valid JSON only, no other text.\n\n{prompt}"}]
    )
    content = r.content[0].text.strip()
    start = content.find("{")
    end = content.rfind("}") + 1
    if start != -1 and end > start:
        content = content[start:end]
    parsed = json.loads(content)
    urgency = parsed.get("urgency", "medium").lower()
    parsed["urgency"] = "high" if "high" in urgency else "low" if "low" in urgency else "medium"
    return json.dumps(parsed)

def run_analyst(name, persona, game_state, history):
    prompt = f"""{persona}

Game state: minute {game_state.get('minute')}, score {game_state.get('score')}, possession {game_state.get('possession_pct')}
History: {history.get('pattern', 'limited data')}

Return JSON with exactly these keys: insight, urgency, action.
urgency must be: high, medium, or low.

Example: {{"insight": "Team losing shape", "urgency": "high", "action": "Switch to 4-4-2"}}"""

    try:
        raw = call_model(prompt)
        with lock:
            results[name] = json.loads(raw)
        print(f"  [{name}] ✓")
    except Exception as e:
        print(f"  [{name}] error: {e}")
        with lock:
            results[name] = {"insight": f"{name} unavailable", "urgency": "low", "action": "monitor"}

def synthesize_parallel(game_state, history):
    start = time.time()
    results.clear()

    threads = []
    for name, persona in ANALYSTS.items():
        t = threading.Thread(target=run_analyst, args=(name, persona, game_state, history))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    analyst_time = round((time.time() - start) * 1000)

    combined_prompt = f"""Three analysts assessed this match at minute {game_state.get('minute')}:

Defensive: {json.dumps(results.get('defensive', {}))}
Offensive: {json.dumps(results.get('offensive', {}))}
Physical: {json.dumps(results.get('physical', {}))}

Historical context: {history.get('pattern', 'no data')}

Pick the MOST URGENT insight. Return JSON with keys: headline, body, urgency, action.
headline: 5 words max. body: 2 sentences max. urgency: high/medium/low.

Example: {{"headline": "Danger window opening", "body": "Possession dropping fast. History shows concede risk.", "urgency": "high", "action": "Drop defensive line now"}}"""

    try:
        raw = call_model(combined_prompt)
        insight = json.loads(raw)
    except Exception as e:
        print(f"  [synthesizer] error: {e}")
        insight = {"headline": "Analysis error", "body": str(e), "urgency": "low", "action": "check logs"}

    total_time = round((time.time() - start) * 1000)
    insight["generated_ms"] = total_time
    insight["analyst_ms"] = analyst_time
    insight["minute"] = game_state.get("minute")

    with open("current_insight.json", "w") as f:
        json.dump(insight, f, indent=2)

    print(f"[synthesis] '{insight.get('headline')}' — {insight.get('urgency')} — {total_time}ms total ({analyst_time}ms analysts)")
    return insight

def run():
    print("[synthesis] starting parallel loop...")
    while True:
        try:
            with open("current_state.json") as f:
                game_state = json.load(f)
            with open("history_context.json") as f:
                history = json.load(f)
            synthesize_parallel(game_state, history)
        except FileNotFoundError:
            print("[synthesis] waiting for state files...")
        except Exception as e:
            print(f"[synthesis] error: {e}")
        time.sleep(30)

if __name__ == "__main__":
    run()



