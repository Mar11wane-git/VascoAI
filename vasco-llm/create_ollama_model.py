"""
Installe Vasco LLM dans Ollama (sur ce PC).

  # v0 : le modèle de base + la personnalité de Vasco (sans entraînement)
  python vasco-llm/create_ollama_model.py

  # après l'entraînement sur Kaggle : le fichier .gguf téléchargé
  python vasco-llm/create_ollama_model.py --gguf C:/chemin/vasco-q4_k_m.gguf --name vasco:v1

Le modèle apparaît ensuite dans Vasco (menu des modèles → « Vasco LLM ») quand Ollama tourne.
"""
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE = "qwen3:4b-instruct"  # même famille que le modèle entraîné (Qwen3 4B Instruct, licence Apache 2.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="Crée le modèle Ollama « vasco ».")
    ap.add_argument("--gguf", help="Fichier .gguf entraîné (sinon : modèle de base + personnalité)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"Modèle Ollama de base (défaut : {DEFAULT_BASE})")
    ap.add_argument("--name", default="vasco", help="Nom du modèle dans Ollama (ex. vasco, vasco:v1)")
    args = ap.parse_args()

    with open(os.path.join(HERE, "persona.txt"), encoding="utf-8") as f:
        persona = f.read().strip().replace('"""', "'''")
    source = os.path.abspath(args.gguf) if args.gguf else args.base
    if args.gguf and not os.path.exists(source):
        sys.exit(f"Fichier introuvable : {source}")

    modelfile = "\n".join([
        f"FROM {source}",
        f'SYSTEM """{persona}"""',
        "PARAMETER temperature 0.6",
        "PARAMETER top_p 0.9",
        "PARAMETER num_ctx 4096",
        "",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False, encoding="utf-8") as f:
        f.write(modelfile)
        path = f.name
    print(f"ollama create {args.name}  (FROM {source})")
    try:
        result = subprocess.run(["ollama", "create", args.name, "-f", path])
    finally:
        os.unlink(path)
    if result.returncode == 0:
        print(f"OK : « {args.name} » est installé. Test rapide : ollama run {args.name} \"salam, chkoun nta?\"")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
