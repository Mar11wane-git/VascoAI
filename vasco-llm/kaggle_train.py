"""
Entraîne Vasco LLM sur Kaggle (GPU T4 gratuit) depuis ce PC, de bout en bout :
  1. envoie build/train.jsonl + eval.jsonl comme dataset privé  <user>/vasco-data
  2. lance train_vasco.ipynb sur GPU T4                          <user>/vasco-train
  3. attend la fin (≈ 1 h), télécharge le .gguf, l'installe dans Ollama (vasco:v1)

Prérequis (une seule fois) :
  - compte Kaggle avec téléphone vérifié (sinon pas de GPU ni d'internet)
  - kaggle.com → Settings → API → « Generate New Token » → copier le token affiché dans
    C:\\Users\\<vous>\\.kaggle\\access_token.txt   (ancien format : kaggle.json dans le même dossier)

  python vasco-llm/prepare_data.py --hf --hf-max 1500
  python vasco-llm/kaggle_train.py                     # tout
  python vasco-llm/kaggle_train.py --resume            # seulement attendre / télécharger le dernier run
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")


def kaggle_cmd():
    exe = shutil.which("kaggle") or os.path.join(os.path.dirname(sys.executable), "kaggle")
    return [exe]


def run(args, check=True, quiet=False):
    p = subprocess.run(kaggle_cmd() + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout + p.stderr).strip()
    if not quiet and out:
        print(out[-2000:])
    if check and p.returncode != 0:
        sys.exit(f"Échec : kaggle {' '.join(args)}")
    return p.returncode, out


def username(cli_user: str) -> str:
    if cli_user:
        return cli_user
    if os.getenv("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    path = os.path.join(os.path.expanduser("~"), ".kaggle", "kaggle.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["username"]
    try:  # nouveau token (~/.kaggle/access_token[.txt] ou KAGGLE_API_TOKEN) : Kaggle donne le nom d'utilisateur
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi()
        api.authenticate()
        if api.config_values.get("username"):
            return api.config_values["username"]
    except BaseException as e:  # authenticate() peut faire sys.exit
        print(f"Authentification Kaggle impossible : {e}")
    sys.exit("Identifiants Kaggle introuvables : collez le token dans ~/.kaggle/access_token.txt (voir l'en-tête du script).")


def push_dataset(user: str) -> None:
    for f in ("train.jsonl", "eval.jsonl"):
        if not os.path.exists(os.path.join(BUILD, f)):
            sys.exit("build/ est vide : lancez d'abord  python vasco-llm/prepare_data.py --hf --hf-max 1500")
    stage = os.path.join(BUILD, "kaggle_data")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    for f in ("train.jsonl", "eval.jsonl"):
        shutil.copy(os.path.join(BUILD, f), stage)
    with open(os.path.join(stage, "dataset-metadata.json"), "w", encoding="utf-8") as f:
        json.dump({"title": "vasco-data", "id": f"{user}/vasco-data", "licenses": [{"name": "other"}]}, f)
    exists = run(["datasets", "status", f"{user}/vasco-data"], check=False, quiet=True)[0] == 0
    if exists:
        print("Mise à jour du dataset privé vasco-data…")
        run(["datasets", "version", "-p", stage, "-m", time.strftime("data %Y-%m-%d %H:%M"), "-q"])
    else:
        print("Création du dataset privé vasco-data…")
        run(["datasets", "create", "-p", stage, "-q"])
    for _ in range(60):  # Kaggle doit finir de traiter les fichiers avant que le notebook les voie
        _, out = run(["datasets", "status", f"{user}/vasco-data"], check=False, quiet=True)
        if "ready" in out.lower():
            print("Dataset prêt.")
            return
        time.sleep(10)
    print("Le dataset n'est pas encore « ready » ; on continue quand même.")


def push_kernel(user: str) -> None:
    stage = os.path.join(BUILD, "kaggle_kernel")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    shutil.copy(os.path.join(HERE, "train_vasco.ipynb"), stage)
    with open(os.path.join(stage, "kernel-metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "id": f"{user}/vasco-train", "title": "vasco-train", "code_file": "train_vasco.ipynb",
            "language": "python", "kernel_type": "notebook", "is_private": True,
            "enable_gpu": True, "enable_internet": True,
            "dataset_sources": [f"{user}/vasco-data"], "competition_sources": [], "kernel_sources": [],
        }, f)
    print("Lancement de l'entraînement sur GPU T4…")
    run(["kernels", "push", "-p", stage, "--accelerator", "NvidiaTeslaT4"])


def wait(user: str) -> str:
    t0, last = time.time(), ""
    while True:
        _, out = run(["kernels", "status", f"{user}/vasco-train"], check=False, quiet=True)
        m = re.search(r'status "?([\w.]+)', out)
        status = (m.group(1) if m else out).split(".")[-1].lower()
        if status != last:
            print(f"[{(time.time() - t0) / 60:5.1f} min] statut : {status}")
            last = status
        if any(s in status for s in ("complete", "error", "cancel")):
            return status
        time.sleep(60)


def download(user: str, name: str, ok: bool) -> None:
    out_dir = os.path.join(BUILD, "kaggle_out")
    os.makedirs(out_dir, exist_ok=True)
    run(["kernels", "output", f"{user}/vasco-train", "-p", out_dir, "--file-pattern", r".*\.log$", "-o"],
        check=False, quiet=True)
    logs = glob.glob(os.path.join(out_dir, "**", "*.log"), recursive=True)
    if not ok:
        if logs:
            with open(logs[0], encoding="utf-8", errors="replace") as f:
                print("--- fin du journal Kaggle ---\n" + f.read()[-4000:])
        sys.exit(f"L'entraînement a échoué. Journal complet : {logs[0] if logs else 'kaggle.com → Code → vasco-train'}")
    print("Téléchargement du modèle .gguf (~2,5 Go)…")
    run(["kernels", "output", f"{user}/vasco-train", "-p", out_dir, "--file-pattern", r".*\.gguf$", "-o"])
    ggufs = sorted(glob.glob(os.path.join(out_dir, "**", "*.gguf"), recursive=True), key=os.path.getsize)
    if not ggufs:
        sys.exit("Aucun .gguf dans la sortie Kaggle : ouvrez kaggle.com → Code → vasco-train pour voir les erreurs.")
    gguf = ggufs[-1]
    print(f"Installation dans Ollama sous le nom {name} : {gguf}")
    subprocess.run([sys.executable, os.path.join(HERE, "create_ollama_model.py"), "--gguf", gguf, "--name", name],
                   check=True)
    print(f"\nTerminé. Comparez : python vasco-llm/eval_compare.py --models {name} qwen3:4b-instruct")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="", help="Nom d'utilisateur Kaggle (sinon lu dans kaggle.json)")
    ap.add_argument("--name", default="mr1:v1", help="Nom du modèle dans Ollama (défaut mr1:v1)")
    ap.add_argument("--resume", action="store_true", help="Ne rien renvoyer : attendre/télécharger le dernier run")
    args = ap.parse_args()
    user = username(args.user)
    if not args.resume:
        push_dataset(user)
        push_kernel(user)
    status = wait(user)
    download(user, args.name, "complete" in status)


if __name__ == "__main__":
    main()
