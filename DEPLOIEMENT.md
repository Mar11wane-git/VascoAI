# Déployer Vasco en ligne

Trois façons de mettre Vasco en ligne :

| Option | Pour qui | Prix | HTTPS | Difficulté |
|---|---|---|---|---|
| **A. Railway** (recommandé) | Mise en ligne rapide, sans gérer de serveur | Payant à l'usage après l'essai (vérifier le tarif actuel sur railway.com) | Automatique | Facile |
| **B. Serveur VPS + Docker** | Contrôle total, coût fixe | Environ 4 à 6 €/mois (Hetzner, OVH, DigitalOcean…) | Automatique (Caddy) | Moyenne |
| **C. Tunnel depuis votre PC** | Montrer l'app quelques heures | Gratuit | Oui | Très facile, mais temporaire |

Avant de commencer, gardez sous la main :
- une clé **Gemini** gratuite (https://aistudio.google.com/apikey), indispensable pour la voix et recommandée pour l'agent ;
- une clé **Pollinations** gratuite (https://enter.pollinations.ai), qui retire l'attente de 45 s du moteur gratuit.

---

## Option A — Railway (recommandé)

Railway lit le `Dockerfile` et le `railway.json` déjà présents dans le projet. Il construit l'interface React, installe Python et lance le serveur. MySQL tourne dans le même projet.

### Étape 1 — Mettre le code sur GitHub (dépôt privé)

1. Créez un compte sur https://github.com, puis un **nouveau dépôt privé** nommé `vasco` (sans README).
2. Dans PowerShell, à la racine du projet :

   ```powershell
   cd C:\Users\HP\Downloads\Vasco
   git init
   git add .
   git status
   ```

   **Vérifiez dans la liste qu'il n'y a ni `.env` ni `.venv`.** Ils sont exclus par `.gitignore` et ne doivent jamais être envoyés, car `.env` contient vos clés et mots de passe.

3. Enregistrez et envoyez :

   ```powershell
   git commit -m "Vasco : première mise en ligne"
   git branch -M main
   git remote add origin https://github.com/VOTRE_COMPTE/vasco.git
   git push -u origin main
   ```

### Étape 2 — Créer le projet Railway

1. Créez un compte sur https://railway.com (« Login with GitHub »).
2. **New Project → Deploy from GitHub repo →** choisissez `vasco` et autorisez l'accès au dépôt.
3. Railway détecte le `Dockerfile` et lance une première construction. Elle échouera au démarrage tant que MySQL n'est pas ajouté : c'est normal.

### Étape 3 — Ajouter la base MySQL

1. Dans le projet : **+ New → Database → Add MySQL**.
2. Attendez que le service MySQL soit vert (« Active »). Les tables de Vasco seront créées automatiquement au premier démarrage.

### Étape 4 — Générer l'adresse publique

1. Cliquez sur le service **vasco**, puis **Settings → Networking → Generate Domain**.
2. Notez l'adresse, par exemple `https://vasco-production.up.railway.app`.

### Étape 5 — Configurer les variables

Service **vasco → Variables → Raw Editor**, collez puis adaptez :

```env
MYSQL_URL=${{MySQL.MYSQL_URL}}
APP_URL=https://vasco-production.up.railway.app
ADMIN_EMAILS=votre.email@gmail.com
GEMINI_API_KEY=votre_cle_gemini
POLLINATIONS_API_KEY=votre_cle_pollinations
AGENT_CODE_EXECUTION=false
```

- `MYSQL_URL=${{MySQL.MYSQL_URL}}` relie automatiquement Vasco à la base. Gardez exactement cette syntaxe.
- `APP_URL` est l'adresse de l'étape 4. Elle sert aux retours de connexion Google et GitHub.
- `ADMIN_EMAILS` : seuls ces comptes peuvent modifier les clés API depuis l'interface. Sans cette variable, personne ne peut les modifier en ligne : ces clés sont partagées par tous les utilisateurs, et un inconnu pourrait les remplacer.
- Optionnel : `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`.
- Laissez `AGENT_CODE_EXECUTION=false` sur un site public : l'exécution de code n'est pas une isolation forte.

Cliquez sur **Deploy**. Dans **Deployments → View logs**, vous devez voir :

```
[Vasco] Base MySQL prête : root@...:3306/railway
Uvicorn running on http://0.0.0.0:...
```

Ouvrez l'adresse publique : la page de connexion s'affiche. **La connexion par e-mail fonctionne déjà.**

### Étape 6 — Connexion Google sur votre domaine

Le client Google intégré au code ne fonctionne qu'en local. En ligne, il faut créer le vôtre (gratuit) :

1. https://console.cloud.google.com → créez un projet `Vasco`.
2. **APIs & Services → OAuth consent screen** : type **External**, nom « Vasco », votre e-mail. Ajoutez les scopes `email`, `profile` et `openid`. Tant que l'app est en mode « Testing », ajoutez vos comptes dans **Test users**, ou cliquez sur **Publish app**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID** :
   - Application type : **Web application** ;
   - Authorized JavaScript origins : `https://vasco-production.up.railway.app` ;
   - Authorized redirect URIs : `https://vasco-production.up.railway.app/api/auth/google/callback`.
4. Copiez l'**ID client** et le **code secret**, puis ajoutez-les dans les variables Railway :

   ```env
   GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=xxxxxxxx
   ```

### Étape 7 — Connexion GitHub (optionnelle)

Sans réglage, le bouton GitHub utilise le « Device Flow » (un code à saisir sur github.com), qui fonctionne partout. Pour une connexion directe :

1. https://github.com/settings/developers → **New OAuth App**.
2. Homepage URL : `https://vasco-production.up.railway.app`, Authorization callback URL : `https://vasco-production.up.railway.app/api/auth/github/callback`.
3. Ajoutez `GITHUB_CLIENT_ID` et `GITHUB_CLIENT_SECRET` dans les variables Railway.

### Étape 8 — Transférer vos comptes existants (optionnel)

Pour retrouver en ligne les comptes de votre MySQL local :

1. Service **MySQL → Variables** : copiez `MYSQL_PUBLIC_URL`, de la forme `mysql://root:MOTDEPASSE@HOTE:PORT/railway`.
2. Exportez la base locale, puis importez-la :

   ```powershell
   $mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin"
   & "$mysql\mysqldump.exe" -uroot -p --no-create-db Vasco > vasco.sql
   Get-Content vasco.sql -Raw | & "$mysql\mysql.exe" -h HOTE -P PORT -uroot -pMOTDEPASSE railway
   ```

3. Supprimez `vasco.sql` ensuite : il contient les données des utilisateurs.

### Étape 9 — Vérifier

- [ ] La page de connexion s'affiche en HTTPS.
- [ ] Inscription et connexion par e-mail fonctionnent.
- [ ] Connexion Google fonctionne (après l'étape 6).
- [ ] Une question à l'agent reçoit une réponse, et les outils s'affichent.
- [ ] Le profil s'ouvre et les modifications sont enregistrées.
- [ ] Le mode vocal fonctionne (le micro exige le HTTPS, fourni par Railway).

### Mettre à jour plus tard

```powershell
git add .
git commit -m "Description du changement"
git push
```

Railway reconstruit et redéploie automatiquement à chaque `git push`.

---

## Option B — Votre propre serveur (VPS) avec Docker

Le projet contient `docker-compose.yml` (MySQL, Vasco et Caddy) et `Caddyfile` (HTTPS automatique avec Let's Encrypt).

1. Louez un VPS **Ubuntu 24.04** avec au moins 2 Go de RAM.
2. Chez votre fournisseur de domaine, créez un enregistrement **DNS A** `vasco.mondomaine.com` qui pointe vers l'adresse IP du VPS.
3. Connectez-vous au VPS et installez Docker :

   ```bash
   ssh root@IP_DU_VPS
   curl -fsSL https://get.docker.com | sh
   ```

4. Récupérez le code depuis GitHub (étape 1 de l'option A) :

   ```bash
   git clone https://github.com/VOTRE_COMPTE/vasco.git
   cd vasco
   ```

5. Créez le fichier `.env` :

   ```bash
   cp .env.example .env
   nano .env
   ```

   À renseigner au minimum :

   ```env
   DOMAIN=vasco.mondomaine.com
   APP_URL=https://vasco.mondomaine.com
   MYSQL_PASSWORD=un_mot_de_passe_long_et_unique
   ADMIN_EMAILS=votre.email@gmail.com
   GEMINI_API_KEY=...
   POLLINATIONS_API_KEY=...
   AGENT_CODE_EXECUTION=false
   ```

6. Démarrez :

   ```bash
   docker compose up -d --build
   docker compose logs -f app
   ```

7. Ouvrez `https://vasco.mondomaine.com`. Le certificat HTTPS est créé automatiquement en quelques secondes.
8. Pour la connexion Google, suivez l'étape 6 de l'option A avec votre domaine.

Pour mettre à jour : `git pull && docker compose up -d --build`.
Pour sauvegarder la base : `docker compose exec db mysqldump -uroot -p Vasco > sauvegarde.sql`.

---

## Option C — Partage temporaire depuis votre PC

Double-cliquez sur `host_public.bat` : il lance Vasco et crée un lien public HTTPS (localhost.run).

Limites :
- le PC doit rester allumé ;
- le lien change à chaque redémarrage ;
- la connexion Google ne fonctionne pas, car l'adresse change (utilisez l'e-mail ou GitHub).

---

## Problèmes fréquents

| Symptôme | Cause et solution |
|---|---|
| Les logs affichent `Connexion MySQL impossible` | `MYSQL_URL` est absent ou mal écrit. Vérifiez `${{MySQL.MYSQL_URL}}` et que le service MySQL est actif. |
| Google affiche `redirect_uri_mismatch` | L'URI dans Google Cloud doit être **exactement** `APP_URL` + `/api/auth/google/callback` (https, sans `/` final dans `APP_URL`). |
| Google affiche « Accès bloqué : app non validée » | Ajoutez le compte dans **Test users**, ou publiez l'écran de consentement. |
| « Seul l'administrateur peut modifier les clés API » | Normal en ligne : ajoutez votre e-mail dans `ADMIN_EMAILS`, ou mettez les clés dans les variables d'environnement. |
| Les clés saisies dans l'interface disparaissent après un redéploiement | Sur Railway, le disque est réinitialisé à chaque déploiement. Mettez les clés dans **Variables**. |
| Le moteur gratuit répond lentement | Ajoutez `POLLINATIONS_API_KEY` ou `GEMINI_API_KEY`. |
| Le micro ne s'active pas | Le site doit être en HTTPS, et le navigateur doit avoir l'autorisation d'utiliser le micro. |
