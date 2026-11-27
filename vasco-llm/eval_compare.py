"""
Compare plusieurs modèles Ollama sur les mêmes questions (eval/questions.csv) et écrit un tableau
côte à côte à noter à la main : eval/resultats.html

  python vasco-llm/eval_compare.py                                  # vasco vs le modèle de base
  python vasco-llm/eval_compare.py --models vasco:v1 vasco qwen3:4b-instruct

Tous les modèles reçoivent la personnalité de Vasco, pour comparer à armes égales.
"""
import argparse
import csv
import html
import os
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))


def ask(base: str, model: str, system: str, question: str) -> tuple:
    t = time.monotonic()
    try:
        r = httpx.post(f"{base}/v1/chat/completions", timeout=600, json={
            "model": model, "temperature": 0.6, "max_tokens": 400,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}],
        })
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:  # modèle absent, Ollama arrêté...
        text = f"[erreur : {e}]"
    return text, time.monotonic() - t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["vasco", "qwen3:4b-instruct"])
    ap.add_argument("--ollama", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"))
    args = ap.parse_args()

    with open(os.path.join(HERE, "persona.txt"), encoding="utf-8") as f:
        system = f.read().strip()
    with open(os.path.join(HERE, "eval", "questions.csv"), encoding="utf-8-sig", newline="") as f:
        questions = [r["question"].strip() for r in csv.DictReader(f)
                     if r.get("question", "").strip() and not r["question"].strip().startswith("<")]

    rows = []
    for q in questions:
        answers = []
        for m in args.models:
            text, secs = ask(args.ollama, m, system, q)
            answers.append((text, secs))
            print(f"[{m}] {secs:5.1f}s  {q[:50]!r} -> {text[:80]!r}")
        rows.append((q, answers))

    head = "".join(f"<th>{html.escape(m)}</th>" for m in args.models)
    body = "".join(
        "<tr><td class=q dir=auto>" + html.escape(q) + "</td>" + "".join(
            f"<td dir=auto>{html.escape(t)}<div class=meta>{s:.1f} s · note : "
            "<select><option></option><option>1</option><option>2</option><option>3</option>"
            "<option>4</option><option>5</option></select></div></td>" for t, s in ans) + "</tr>"
        for q, ans in rows)
    page = f"""<!doctype html><meta charset=utf-8><title>Vasco LLM — comparaison</title>
<style>body{{font:15px system-ui;margin:20px;background:#0b0a14;color:#eee}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #333;padding:10px;vertical-align:top;white-space:pre-wrap}}th{{background:#1d1a33}}
.q{{font-weight:600;width:18%}}.meta{{color:#9a98b5;font-size:12px;margin-top:8px}}</style>
<h1>Vasco LLM — comparaison ({len(rows)} questions)</h1><table><tr><th>Question</th>{head}</tr>{body}</table>"""
    out = os.path.join(HERE, "eval", "resultats.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"\nOuvrez {out} pour comparer et noter les réponses.")


if __name__ == "__main__":
    main()
