"""
Prépare les données d'entraînement de Vasco LLM  ->  build/train.jsonl + build/eval.jsonl

Sources :
  1. VOS données (prioritaires) : vasco-llm/data/*.csv  (colonnes question,reponse[,categorie])
                                   vasco-llm/data/*.jsonl (une conversation par ligne : {"messages": [...]})
     Les lignes modèles qui commencent par « < » sont ignorées.
  2. Option --hf : des sous-ensembles du dataset public MBZUAI-Paris/Darija-SFT-Mixture.
     Ce dataset mélange des licences (certaines parties sont NON commerciales) et des parties
     traduites/générées par Claude et GPT-4 (interdites pour entraîner un modèle concurrent).
     On ne garde donc QUE les sous-ensembles de la liste HF_SUBSETS (écrits par des humains,
     licence libre). Vérifiez-la avec --list-subsets avant de l'utiliser.

  python vasco-llm/prepare_data.py                  # vos données seulement
  python vasco-llm/prepare_data.py --list-subsets   # affiche les sous-ensembles HF disponibles
  python vasco-llm/prepare_data.py --hf --hf-max 20000

Chaque exemple reçoit la personnalité de Vasco (persona.txt) comme message système : le modèle
apprend à répondre comme Vasco avec exactement le prompt qu'il aura ensuite dans Ollama.
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BUILD = os.path.join(HERE, "build")
HF_DATASET = "MBZUAI-Paris/Darija-SFT-Mixture"
# Sous-ensembles retenus (texte humain, licence libre d'après la fiche du dataset) : traductions
# FLORES+ / NLLB-Seed (CC BY-SA 4.0) et résumés MArSum (CC BY 4.0). Le nom exact se vérifie avec
# --list-subsets ; un sous-ensemble est retenu si son nom contient un de ces mots.
HF_SUBSETS = ("flores", "nllb", "marsum")
USER_REPEAT = 3  # vos exemples comptent 3 fois : ce sont eux qui donnent le style de Vasco


def persona() -> str:
    with open(os.path.join(HERE, "persona.txt"), encoding="utf-8") as f:
        return f.read().strip()


def clean(text) -> str:
    return " ".join(str(text or "").replace("\r", "").split(" ")).strip()


def load_user_data():
    convs, skipped = [], 0
    for path in sorted(glob.glob(os.path.join(DATA, "*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                q, a = clean(row.get("question")), clean(row.get("reponse"))
                if not q or not a or q.startswith("<") or a.startswith("<"):
                    skipped += 1
                    continue
                convs.append([{"role": "user", "content": q}, {"role": "assistant", "content": a}])
    for path in sorted(glob.glob(os.path.join(DATA, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    msgs = json.loads(line)["messages"]
                except (ValueError, KeyError):
                    print(f"  ! {os.path.basename(path)} ligne {n} ignorée (format attendu : {{\"messages\": [...]}})")
                    continue
                msgs = [{"role": m["role"], "content": clean(m["content"])} for m in msgs
                        if m.get("role") in ("user", "assistant") and clean(m.get("content"))]
                if len(msgs) >= 2 and msgs[-1]["role"] == "assistant":
                    convs.append(msgs)
    return convs, skipped


def load_hf(max_rows: int, list_only: bool):
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("Installez d'abord : pip install datasets")
    ds = load_dataset(HF_DATASET, split="train")
    counts = {}
    for name in ds["dataset"]:
        counts[name] = counts.get(name, 0) + 1
    print(f"\nSous-ensembles de {HF_DATASET} :")
    for name, c in sorted(counts.items(), key=lambda x: -x[1]):
        keep = any(k in name.lower() for k in HF_SUBSETS)
        print(f"  {'[GARDÉ] ' if keep else '        '}{name:40s} {c:>7}")
    if list_only:
        return []
    keep_names = {n for n in counts if any(k in n.lower() for k in HF_SUBSETS)}
    rows = ds.filter(lambda r: r["dataset"] in keep_names)
    convs = []
    for r in rows:
        msgs = [{"role": m["role"], "content": clean(m["content"])} for m in r["messages"]
                if m.get("role") in ("user", "assistant") and clean(m.get("content"))]
        if len(msgs) >= 2 and msgs[-1]["role"] == "assistant":
            convs.append(msgs)
    random.shuffle(convs)
    return convs[:max_rows]


def key(conv) -> str:
    return hashlib.sha1(json.dumps(conv, ensure_ascii=False).encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hf", action="store_true", help=f"Ajouter des sous-ensembles libres de {HF_DATASET}")
    ap.add_argument("--hf-max", type=int, default=20000, help="Nombre max d'exemples HF (défaut 20000)")
    ap.add_argument("--list-subsets", action="store_true", help="Afficher les sous-ensembles HF et quitter")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    if args.list_subsets:
        load_hf(0, True)
        return

    user, skipped = load_user_data()
    uniq = {key(c): c for c in user}
    user = list(uniq.values())
    print(f"Vos exemples : {len(user)} (lignes modèles/vides ignorées : {skipped})")
    if len(user) < 50:
        print("  Conseil : visez au moins 300 à 1000 exemples écrits à la main pour un vrai style Vasco.")

    random.shuffle(user)
    n_eval = max(1, len(user) // 10) if len(user) >= 10 else 0
    eval_set, user_train = user[:n_eval], user[n_eval:]

    hf = load_hf(args.hf_max, False) if args.hf else []
    if args.hf:
        print(f"Exemples HF gardés : {len(hf)}")

    system = {"role": "system", "content": persona()}
    train = [[system] + c for c in user_train for _ in range(USER_REPEAT)] + [[system] + c for c in hf]
    random.shuffle(train)
    if not train:
        sys.exit("Aucune donnée : remplissez vasco-llm/data/mes_donnees.csv (ou utilisez --hf).")

    os.makedirs(BUILD, exist_ok=True)
    for name, rows in (("train.jsonl", train), ("eval.jsonl", [[system] + c for c in eval_set])):
        with open(os.path.join(BUILD, name), "w", encoding="utf-8") as f:
            for conv in rows:
                f.write(json.dumps({"messages": conv}, ensure_ascii=False) + "\n")
    print(f"\nÉcrit : build/train.jsonl ({len(train)} exemples), build/eval.jsonl ({len(eval_set)} exemples)")
    print("Étape suivante : envoyer le dossier build/ sur Kaggle et lancer train_vasco.ipynb (voir README).")


if __name__ == "__main__":
    main()
