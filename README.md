# ✦ Vasco AI — Agent IA autonome

**Vasco** est un agent IA qui ne se contente pas de répondre : il **planifie, utilise des outils, lit les résultats et recommence** jusqu'à pouvoir donner une réponse vérifiée et sourcée. Chaque étape (réflexion, appel d'outil, résultat) s'affiche **en direct** dans l'interface.

Il comprend et répond en **Français, Darija marocaine, Arabe et Anglais**.

## 🧠 Comment fonctionne l'agent

```
Question ─► Modèle ─► appels d'outils (en parallèle) ─► résultats ─► Modèle ─► … ─► Réponse finale
```

- **Boucle agentique** avec appel d'outils natif (function calling) chez tous les fournisseurs, jusqu'à 8 étapes, puis réponse forcée.
- **Streaming temps réel** (Server-Sent Events) : réflexion, outils, tokens de la réponse.
- **Bouton Stop**, **Régénérer**, statistiques par réponse (étapes, outils, durée).
- Sources numérotées et cliquables sous chaque réponse.

### Outils

| Outil | Rôle |
|---|---|
| 🔎 `web_search` | Recherche Web et actualités (DuckDuckGo, Google News, Wikipedia) |
| 📖 `fetch_url` | Lit une page Web ou un PDF en entier (protection SSRF : hôtes privés refusés) |
| 🧮 `calculator` | Calcul exact sécurisé (évaluation AST, jamais `eval`) |
| 🕒 `get_datetime` | Date et heure, par fuseau horaire |
| 🎨 `generate_image` | Génération d'images |
| 🐍 `run_python` | Exécution de code Python *(désactivé par défaut, voir `.env.example`)* |

## 🔌 Modèles

| Fournisseur | Modèles | Clé |
|---|---|---|
| ⚡ Vasco Cloud | GPT-OSS via Pollinations | Aucune (clé gratuite recommandée) |
| 🟠 Anthropic | Claude Opus 5.5, Sonnet 5.5, Haiku 5.5 | `ANTHROPIC_API_KEY` |
| 🟢 OpenAI | GPT-5, GPT-5 mini, GPT-4.1 | `OPENAI_API_KEY` |
| ✨ Google | Gemini 2.5 Pro / Flash / Flash-Lite | `GEMINI_API_KEY` |
| 🐋 DeepSeek | Chat, Reasoner | `DEEPSEEK_API_KEY` |
| 🌐 OpenRouter | Tout le catalogue | `OPENROUTER_API_KEY` |
| 🦙 Ollama | Modèles locaux (Qwen 3, Llama…) | — |

N'importe quel autre ID de modèle peut être saisi dans le sélecteur (« Autre modèle »).

> **Mode gratuit sans clé :** le moteur gratuit limite à **1 réponse toutes les 45 s par IP**. L'agent fait alors un seul tour d'outils puis répond (≈ 45-60 s quand un outil est utilisé). Une clé gratuite sur [enter.pollinations.ai](https://enter.pollinations.ai), ou une clé Gemini, supprime cette attente. Le petit modèle gratuit peut aussi faire des erreurs de calcul ; les modèles Claude, GPT ou Gemini suivent bien mieux les consignes.

## 🚀 Lancer Vasco

**Windows :** double-cliquez sur `start_vasco.bat`.

**Ligne de commande :**
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```
Puis ouvrez **http://localhost:8000**.

**Développement de l'interface :**
```powershell
cd react-app; npm install; npm run dev     # http://localhost:5173
npm run build                              # régénère /frontend servi par FastAPI
```

## 🗂️ Structure

```
backend/
  agent/
    tools.py     registre des outils (schémas JSON, validation, sécurité)
    llm.py       adaptateurs streaming par fournisseur (OpenAI-compatible, Claude SDK, Gemini)
    runner.py    boucle de l'agent + événements SSE
  main.py        API FastAPI (/api/agent/stream, auth, upload, TTS…)
react-app/src/
  components/AgentMessage.jsx   timeline : réflexion, cartes d'outils, réponse, sources
  lib/api.js                    client SSE authentifié
  lib/markdown.js               rendu Markdown assaini (DOMPurify)
```

## 🔒 Sécurité

- Les endpoints de l'agent, de l'upload, du TTS et des clés API exigent une session (`Authorization: Bearer`).
- Le HTML produit par le modèle est assaini (DOMPurify) avant affichage.
- `fetch_url` refuse localhost, les réseaux privés et les adresses de métadonnées cloud, à chaque redirection.
- Les outils désactivés sont refusés à l'exécution, même si le modèle tente de les appeler.

## Base de données MySQL

Les comptes, sessions, profils, préférences et statistiques sont stockés dans MySQL (base `Vasco`).
Configuration dans `.env` : `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`.
Les tables sont créées automatiquement au démarrage (`users`, `sessions`, `user_profiles`, `user_stats`, `user_daily_usage`, `user_activity`, `conversations`).
L'ancien fichier `backend/users_db.json` est importé une seule fois puis renommé en `users_db.migrated.json`.

Les discussions sont synchronisées avec le compte (table `conversations`, `/api/conversations`) : elles suivent
l'utilisateur sur tous ses appareils. Le navigateur garde aussi une copie locale, qui sert hors ligne.

## ✅ Tests et déploiement

```powershell
python -m pytest tests -q              # API, auth, discussions, outils, repli Pollinations (base MySQL "vasco_test")
cd react-app; npm test                 # fusion des discussions entre appareils
powershell -ExecutionPolicy Bypass -File deploy.ps1   # tests + build + Vercel, annulé si un test échoue
```

Les mêmes tests tournent sur GitHub Actions à chaque push (`.github/workflows/ci.yml`, avec un MySQL de test).

## 🛟 Services gratuits et replis

- **Vasco Cloud** utilise l'API Pollinations avec clé (`POLLINATIONS_API_KEY`). Si la clé ne marche plus (pollen
  épuisé, clé révoquée, panne), il bascule seul sur l'API gratuite sans clé (historique raccourci, plus lent),
  puis sur un moteur de secours.
- **Images / vidéos** : même repli, clé puis API anonyme (une image toutes les ~45 s).
