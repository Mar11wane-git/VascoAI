"""
Installe Vasco LLM dans Ollama (sur ce PC).

  # v0 : le modèle de base + la personnalité de Vasco (sans entraînement)
  python vasco-llm/create_ollama_model.py

  # après l'entraînement sur Kaggle : le fichier .gguf téléchargé
  python vasco-llm/create_ollama_model.py --gguf C:/chemin/vasco-q4_k_m.gguf --name mr1:v1

Le modèle apparaît ensuite dans Vasco (menu des modèles → « Vasco LLM ») quand Ollama tourne.
"""
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE = "qwen3:4b-instruct"  # même famille que le modèle entraîné (Qwen3 4B Instruct, licence Apache 2.0)


def base_template_lines(base: str) -> list:
    """TEMPLATE and stop lines copied from an installed Ollama model (`ollama pull <base>` first)."""
    def show(flag: str) -> str:
        r = subprocess.run(["ollama", "show", base, flag], capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            sys.exit(f"Modèle de base introuvable dans Ollama ({base}) : lancez d'abord  ollama pull {base}")
        return r.stdout
    # `--template` can return the GGUF's Jinja template; the Modelfile holds the Go template Ollama runs.
    modelfile = show("--modelfile")
    start = modelfile.find('TEMPLATE """')
    end = modelfile.find('"""', start + 12)
    if start < 0 or end < 0:
        sys.exit(f"Template introuvable pour {base}")
    template = modelfile[start + 12:end]
    stops = [ln.strip() for ln in show("--parameters").splitlines() if ln.strip().startswith("stop")]
    return [f'TEMPLATE """{template}"""'] + [f"PARAMETER {s}" for s in stops]


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

    lines = [f"FROM {source}"]
    if args.gguf:
        # A bare GGUF has no chat template in Ollama: without it the model sees raw text, never stops
        # and spits out tool tags. Reuse the base model's template (Qwen3 ChatML, tool calls) and stop tokens.
        lines += base_template_lines(args.base)
    lines += [
        f'SYSTEM """{persona}"""',
        "PARAMETER temperature 0.6",
        "PARAMETER top_p 0.9",
        "PARAMETER num_ctx 4096",
        "",
    ]
    modelfile = "\n".join(lines)
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
