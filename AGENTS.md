# AGENTS.md — contrat contributeur (dépôt Rust uniquement)

L'implémentation Python (`src/n8n_launcher/`, `tests/`, `pyproject.toml`, `uv.lock`,
`run.py`, PyInstaller), les trois crates PyO3 et `MIGRATION.md` ont été supprimés.
Il n'existe **plus** de chemin Python dans ce dépôt : tout le code livré est sous
`rust/n8n-launcher-workspace/`. N'ajoutez pas de module Python, de bridge PyO3 ni
descript de build PyInstaller pour « faciliter » une tâche.

## Où vit le code

`rust/n8n-launcher-workspace/` est un workspace Cargo (resolver 2, édition 2024,
`rust-version = "1.97"`, `[workspace.package] version = "6.0.0"`) avec un seul
membre, `n8n-launcher`. Le crate hérite sa version (`version.workspace = true`),
expose une bibliothèque et un binaire `n8n-launcher` avec `--self-test`.
Features : `default = ["gui"]`, `gui = []` — un build par défaut doit produire un
binaire capable d'ouvrir une fenêtre, pas un exécutable qui refuse de démarrer.

Dépendances structurantes : `serde`/`serde_json`, `clap` 4, `iced` 0.13 (tokio),
`reqwest` (blocking), `rusqlite` (bundled), `bcrypt`, `tracing`, `regex`,
`tempfile`, `ctrlc`.

Packages (`n8n-launcher/src/`) : `main.rs`, `app.rs`, `core/`, `database/`,
`docker/`, `git/`, `github/`, `monitoring/`, `n8n/`, `platform/`, `remote/`,
`workspaces/`. La carte module → responsabilité et les faits mesurés sont dans
`docs/architecture.md` — lisez-le avant de toucher à `n8n/`, `remote/`, `docker/`,
`git/`, `github/`, `monitoring/` ou `app.rs`. `README.md` et `PRODUCT_SUMMARY.md`
décrivent le comportement attendu, pas l'implémentation.

`tests/docker_integration.rs` est le seul test hors des fichiers source : 3 tests
qui s'auto-ignorent sans daemon Docker. Les 142 autres tests sont inline, dans
un `#[cfg(test)] mod tests` en fin de chaque module (145 au total).

## Commandes

```bash
cd rust/n8n-launcher-workspace
cargo test --workspace --all-features   # suite complète (inclut iced)
cargo test --test docker_integration    # nécessite un daemon Docker, sinon skip
cargo build --release
bash scripts/build.sh                   # copie le binaire dans dist/
dist/n8n-launcher --self-test           # vérifie l'artefact fini
```

CI : `.github/workflows/rust.yml` (PR + push sur `dev`/`main`, chemins `rust/**`
et workflows) — `cargo test --workspace --all-features` sur 3 OS, intégration sur
Ubuntu, build release + smoke `--self-test` sur 3 OS, upload des binaires.
`.github/workflows/release.yml` (push `main`) lit la version du workspace Cargo,
la compare au dernier tag `v<version>` et publie uniquement si elle a changé.
Aucune étape `cargo fmt --check` ni `cargo clippy` n'est configurée dans ces
workflows : ce n'est pas un oubli de documentation, c'est une lacune connue
(les composants de toolchain n'étaient pas disponibles localement). Ne prétendez
pas qu'un tel gate existe.

La version est une édition manuelle dans `rust/n8n-launcher-workspace/Cargo.toml`
(actuellement `6.0.0`) ; `platform/updater.rs` la lit via `env!("CARGO_PKG_VERSION")`.

Ne jamais committer `state/`, `workspaces/`, `*.env`, tokens ou credentials.

## Conventions (vérifiées dans le code source)

- **Documentation et tests.** Chaque nouvel item public doit porter un
  commentaire de documentation `///` expliquant le *pourquoi*. Une nouvelle fonction publique
  ships avec son test unitaire dans le même fichier, sous `#[cfg(test)] mod tests`.
  Un test qui n'exerce que le code testé est préférable à un test d'intégration.
- **Contenu généré = template brut + jetons, jamais `format!`.**
  `workspaces/ci.rs` utilise des `r#"..."#` (`__MARKER__`, `__N8N_IMAGE__`) et
  `remote/deploy.rs` des fichiers `include_str!` (`templates/hook.txt`,
  `templates/deploy.txt`) avec `.replace("__TOKEN__", …)`. Accolades et `$`
  doivent survivre tels quels dans les scripts générés.
- **Une seule règle par décision.** Les libellés, tons et règles d'action vivent
  dans `workspaces/status.rs` (`can_start` / `can_stop` / `can_delete` /
  `can_open`, `primary_action`, `card_actions`), toolkit-free ; la vue iced ne
  les réinvente pas. Il n'y a pas de règle de dimensionnement/table dans le port
  Rust : n'en introduisez pas une seconde si vous reconstruisez une vue.
- **Un arrêt demandé par l'utilisateur est une fermeture.** Passez par
  `workspaces::manager::stop_with_sync` (export des workflows + git push avant
  `docker down`) — c'est ce que font `app::close_session` et `app::stop_all`.
  `stop` seul est le teardown interne. `can_stop` est volontairement large :
  tout état sauf `STOPPED` et `STOPPING`, car un démarrage raté laisse des
  conteneurs up et un conteneur en crash-loop est annoncé `STARTING`.
- **Modèle de branches.** `dev` est la branche de travail (`WORKSPACE_BRANCH`,
  `workspace_branch`, `ensure_workspace_branch`) ; `main` n'existe que comme
  référence de production sur le bare repo du serveur (`publish` pousse
  `dev → main` avec un timeout de 300 s, car le hook `post-receive` fait
  Compose up + import de façon synchrone).
- **Token GitHub jamais dans `.git/config`.** `tokenize_remote_url` insère le
  token dans une URL utilisée pour un seul `git push` sans `-u` ; le `remote_url`
  stocké reste propre. (Ce chemin n'a pas encore d'appelant — voir lacunes.)
- **Journal et bracketing de session.** `main()` trace `Surveillance active` et
  `app::close_session` trace `Surveillance terminée` (durée + workspaces encore
  up) après le teardown et avant la fermeture du store. La paire existe dans le
  code ; sa persistance dans `EventStore` n'est pas encore câblée.
- **Git sérialisé par workspace** via `workspace_git_lock` sur
  `.n8n-launcher.git.lock`, fichier exclu du versionnement par `EXCLUDE_BODY`.
  Le lock est *best effort* et non bloquant : ne comptez pas dessus comme sur
  une exclusion mutuelle garantie.
- **Déduplication des workflows par basename** en faveur de `n8nPipelines/` :
  `workspaces/ci.rs::collect_workflows` et la `collect_workflow_files()` du
  `deploy.py` généré. Les exports miroirs à la racine ne doivent jamais être
  importés ou uploadés deux fois.
- **Hook distant.** Il commence par `cd "$HOME"` (git exécute les hooks avec le
  cwd du bare repo alors que tous les chemins générés sont home-relative),
  cite les chemins avec `sh_quote`/`sh_sq`, et écrit `last-deploy.json` de
  façon atomique (`cat > …tmp && mv -f`, `os.replace` côté Python généré).
- **Secrets.** `monitoring/redaction.rs` masque les valeurs avant persistance ;
  ne loggez jamais de token, mot de passe ou URL tokenisée.

## Lacunes connues (vérifiées, ne pas présenter comme finies)

La vue iced a désormais cartes, actions branchées au manager en tâche
asynchrone, recherche, filtres, compteurs, journal, réglages et wizard de
premier lancement ; il reste l'absence de transitions CSS et une grille à deux
colonnes fixes. `resolve_github_token` renvoie toujours `None` ;
`remote::ssh::scp` renvoie `Err("Not implemented")` ; `capture_exceptions` est
un no-op (les événements de session et les actions UI sont, eux, persistés) ;
`core/templates.rs::read_template`
est inutilisé et lit un dossier inexistant ; aucun packaging `.dmg`/AppImage
(`scripts/build.sh` copie seulement le binaire). Détails dans
`docs/architecture.md` §Lacunes.
