# 🇲🇦 Vasco LLM — l-modèle dyal Vasco

Vasco LLM howa **modèle open-source (Qwen3 4B Instruct, licence Apache 2.0)** li kan-trainiwh
(fine-tuning LoRA) 3la darija w 3la chakhsiyat Vasco. Poids dyalo dyalek nta.

- **Ma ghadich ykon a9wa mn GPT/Gemini f kolchi.** Hadok mtrainyin b mi2at malayin dyal dollars.
- **Ymken ykon 7sen menhom f darija, f l-context l-maghribi w f style dyal Vasco.** Hadchi kayt-mesura b `eval_compare.py`.

## Les étapes

| # | Fin | Chno katdir |
|---|-----|-------------|
| 0 | PC | `python vasco-llm/create_ollama_model.py`, ya3ni **v0** : modèle de base + chakhsiyat Vasco, bla training. Kayban f Vasco → menu → « Vasco LLM ». |
| 1 | PC | Kteb l-data f `data/mes_donnees.csv` (question, reponse). Wla 7ett lot dyal as2ila w ajwiba f `data/lotN.txt` (b format « 12. sou2al؟ jawab ») w lanci `python vasco-llm/txt_to_csv.py`. Ila 3endek mo7adathat kamlin, dirhom f `data/*.jsonl` (`{"messages": [...]}`). |
| 2 | PC | `python vasco-llm/prepare_data.py --hf --hf-max 1500` : data dyalek + 1500 exemple darija libre. Kaykhrej `build/train.jsonl` w `build/eval.jsonl`. Ma tkebberch `--hf-max` bzaf, sinon Vasco ywelli ghir kaytarjem. |
| 3 | Kaggle | **Automatique :** 7ett `kaggle.json` f `C:\Users\<nta>\.kaggle\`, men b3d `python vasco-llm/kaggle_train.py`. Kaysift data, kaytrainer f GPU T4 (≈ 1 h), w kay-installi **mr1:v1** (MR1 v1) f Ollama. **Yadawi :** sift `build/` k dataset **vasco-data** w lanci `train_vasco.ipynb` b GPU T4. |
| 4 | PC | (Ghir f l-mode yadawi.) Telecharger l-`.gguf`, men b3d `python vasco-llm/create_ollama_model.py --gguf fichier.gguf --name mr1:v1`. |
| 5 | PC | `python vasco-llm/eval_compare.py --models mr1:v1 vasco qwen3:4b-instruct`, men b3d 7el `eval/resultats.html` w 3ti notes. |

## L-data : 9awa3id mohimma

- **Ma tsta3melch ajwibat ChatGPT, Gemini wla Claude** bach tsayeb l-data. Les conditions dyalhom kaymn3o t-trainer modèle kayzaḥmhom.
- **L-kalita a7sen mn l-kammiya.** 500 jawab mzyan mktoub b yed a7sen mn 50 000 jawab mkhrbe9. Bda b 300-1000 exemple.
- **Nawe3** : sou2al 3adi, idara, se7a (b l-hdar), tkhra, code, chi haja li ma ta3rafhach (bach yt3llem ygoul « ma 3reftch »), w as2ila 3la Vasco b rasso.
- **Kteb b jouj l-7orouf** : l-3arabiya (شنو) w l-latiniya (chno).
- **Ma t7ettch as2ila dyal `eval/questions.csv` f training.** Ila 7ettitihom, l-test ma yb9ach yseddeq.
- **Dataset Darija-SFT-Mixture** (`--hf`) : `prepare_data.py` kayakhed **ghir** les sous-ensembles li ktebhom bnadem w licence dyalhom 7orra (FLORES+, NLLB-Seed, MArSum). Chouf la liste kamla b `--list-subsets`.

## Bach ykhdem l ga3 les utilisateurs online

Daba Vasco LLM kaykhdem ghir f PC dyalek (Ollama). Vercel ma ymkench ykhdem LLM.
Bach ykhdem online, khass serveur fih GPU kay3ti API compatible OpenAI. Men b3d f Vercel zid :

```
VASCO_LLM_URL   = https://<serveur>/v1
VASCO_LLM_KEY   = <clé du serveur>
VASCO_LLM_MODEL = <nom du modèle sur le serveur>
```

Vasco LLM kayban f l-menu ghir ila kan disponible : `VASCO_LLM_URL` mdefini, wla modèle `vasco` installé f Ollama.
