"""
Transforme un lot de questions/réponses collé en texte (data/*.txt) en CSV (data/*.csv) pour prepare_data.py.

Formats acceptés (mélangeables dans le même fichier) :
    12. شنو هي عاصمة كندا؟            12. شنو هي عاصمة كندا؟ أوتاوا.
    أوتاوا.
Les titres de sections du type « 2. العلوم والطبيعة (26–50) » et les numéros seuls sont ignorés.

    python vasco-llm/txt_to_csv.py                 # tous les data/*.txt
    python vasco-llm/txt_to_csv.py data/lot2.txt   # un seul fichier
"""
import csv
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NUM = re.compile(r"^\s*\d+\s*[.)\-–]\s*")
SECTION = re.compile(r"\(\s*\d+\s*[–\-]\s*\d+\s*\)\s*$")
QMARK = re.compile(r"[؟?]")


def parse(text: str):
    pairs, q, a = [], None, []
    problems = []

    def push():
        if q is None:
            return
        ans = " ".join(x for x in a if x).strip()
        if ans:
            pairs.append((q, ans))
        else:
            problems.append(q)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or SECTION.search(line):
            continue
        body = NUM.sub("", line).strip()
        if not body:  # numéro tout seul
            continue
        numbered = body != line
        m = QMARK.search(body)
        if m and (numbered or q is None or a):  # nouvelle question
            push()
            q, rest = body[: m.end()].strip(), body[m.end():].strip()
            a = [rest] if rest else []
        elif q is not None:
            a.append(body)
    push()
    return pairs, problems


def convert(path: str) -> None:
    with open(path, encoding="utf-8-sig") as f:
        pairs, problems = parse(f.read())
    seen, rows = set(), []
    for q, a in pairs:
        if q not in seen:
            seen.add(q)
            rows.append((q, a))
    out = os.path.splitext(path)[0] + ".csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "reponse"])
        w.writerows(rows)
    print(f"{os.path.basename(path)} -> {os.path.basename(out)} : {len(rows)} paires"
          f" ({len(pairs) - len(rows)} doublons retirés)")
    for q in problems:
        print(f"  ! question sans réponse ignorée : {q}")


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(HERE, "data", "*.txt")))
    for p in files:
        convert(p if os.path.isabs(p) else os.path.join(HERE, p) if not os.path.exists(p) else p)
