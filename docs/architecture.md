# docs/architecture.md — carte des modules et faits mesurés (port Rust)

Ce fichier répond à deux questions qu'on ne pose qu'une fois par tâche : *où vit
tel comportement* et *quel fait a été mesuré sur un vrai hôte n8n*. Il remplace
la version Python de ce document et ne conserve que ce qui est vérifiable dans
le code Rust actuel.

**Lisez-le** avant de toucher à `n8n/`, `remote/`, `docker/`, `git/`, `github/`,
`monitoring/`, `database/`, `platform/` ou `app.rs`. **Ne le lisez pas** pour
savoir lancer les tests — c'est `AGENTS.md` §Commandes.

Les chemins ci-dessous sont relatifs à
`rust/n8n-launcher-workspace/n8n-launcher/src/`.

---

## Carte des modules

| Module | Responsabilité |
|---|---|
| `main.rs` | Entrée. `--self-test` court-circuite config, lock et Docker ; sinon lock mono-instance → résolution Docker → journal → chargement config → interface ou premier lancement → arrêt ordonné. Distingue config absente (wizard) et config illisible (backup `launcher.db.corrupt-<ts>` puis arrêt). Trace `Surveillance active` / `Surveillance terminée`. |
| `app.rs` | Coquille iced : `run_gui`, `LauncherState`/`Message`/`update`/`view`/`theme`/`subscription`/`window` (1024×640), drapeau de shutdown, tick de 200 ms, handlers `ctrlc`, `close_session`/`stop_all` (via `stop_with_sync`), `prompt_first_launch`, `self_test`. |
| `core/config.rs` | `ConfigStore` SQLite (`launcher.db`, WAL, busy timeout 10 s, `meta` + `workspaces`, droits `0600`). `load`/`mutate`, migration unique du `config.json` legacy, `backup_unreadable`. `close()` est un no-op (pas de connexion persistante). |
| `core/filelock.rs` | `FileLock` réentrant sur `flock`/verrou OS, `acquire_single_instance_lock` (`launcher.lock`). |
| `core/first_launch.rs` | Règles toolkit-free du wizard : `validate_password` (8–64, chiffre, majuscule), `build_initial_config`, `run_first_launch` (refuse sans Docker puis écrit la config). |
| `core/models.rs` | Dataclasses sérialisables : `DbMode`, `WorkspaceState` (STOPPED/STARTING/RUNNING/STOPPING/ERROR), `DbConfig`, `GitConfig`, `ServerConfig`, `Workspace`, `AppConfig`. |
| `core/paths.rs` | Répertoires `platformdirs` (`directories::ProjectDirs`) : config, logs, runtime, `compose_file(workspace_id)`, `browser_profile_dir`, `updates_dir`. |
| `core/templates.rs` | `read_template` — loader inutilisé, voir lacunes. Les vrais templates sont embarqués par `include_str!` dans `remote/deploy.rs`. |
| `database/credentials.rs` | Création du credential n8n pour la base de données ; `data_db_target`. |
| `database/layout.rs` / `migrations.rs` | `has_db_layout` (`db/schema.sql` ou `db/migrations/*.sql`), détection et exécution SQL via `docker compose exec … psql`. |
| `docker/compose.rs` | Rendu du Compose YAML, un projet par workspace (`n8n-ws-<id>`), volumes `n8ndata-<id>` / `pgdata-<id>`, `DbMode::NONE` sans service DB, `render_remote_compose` avec `name:` stable. |
| `docker/manager.rs` | Wrapper subprocess `docker compose` (`-p`, `-f`), parsing des états et des labels, `resolve_docker_command` (sonde les emplacements macOS avant `PATH`). |
| `git/manager.rs` | Opérations git du dossier de workflows. `WORKSPACE_BRANCH = "dev"`, `workspace_git_lock`, `git_push`, `git_push_ref` (300 s à la publication), `git_seed_remote` + `tokenize_remote_url`, `ensure_workspace_branch`, `EXCLUDE_BODY`. |
| `github/api.rs` / `auth.rs` | Client REST GitHub (owner, repos, runs, jobs, logs, dispatch). `resolve_github_token` est un stub — voir lacunes. Les chemins de repo gardent un `/` littéral (`repos/owner/repo/...`). |
| `monitoring/events.rs` / `store.rs` | Événement structuré et `EventStore` (SQLite dans `logs_dir()/events.db`, rétention 30 j, `latest_events`/`search_events`/`export_events`). Le filtrage est unique et en SQL. |
| `monitoring/redaction.rs` | Masquage des secrets avant persistance (regex sur clés, assignments, Bearer, tokens, mots de passe d'URL). |
| `monitoring/bootstrap.rs` | `bootstrap_logging` crée le store ; `capture_exceptions` est un no-op. |
| `n8n/api.rs` | Client de l'API **publique** (`X-N8N-API-KEY`) : workflows et credentials, erreurs typées avec code HTTP. |
| `n8n/exports.rs` | Contrat d'import/export : `CANONICAL_DIRNAME = "n8nPipelines"`, `WORKFLOW_KEYS` (whitelist du POST), noms de fichiers `<name>-<id>.json`. Le helper `export_path` n'est pas appelé : `workflows.rs::export_all` écrit dans le dossier que lui passe l'appelant (les appelants passent `n8nPipelines/` et la racine en miroir). |
| `n8n/owner.rs` | Bootstrap propriétaire via `/rest/*`, `wait_for_n8n` sur `/healthz/readiness`, bcrypt, création de clé API avec scopes. |
| `n8n/scopes.rs` | Liste de scopes locale — attention : diverge de celle du script distant (voir lacunes). |
| `n8n/workflows.rs` | `SyncRunner`, `import_all`, `export_all(mirror)` : écrit dans `n8nPipelines/` et reflète une copie à la racine. |
| `platform/ports.rs` | `is_port_available`, `suggest_port`. |
| `platform/updater.rs` / `update_flow.rs` | Vérification GitHub Releases, comparaison avec `CARGO_PKG_VERSION`, téléchargement, script d'auto-installation ; `UpdateController` injecte chaque question à l'appelant (callbacks, file d'événements). |
| `remote/ssh.rs` | `ssh_run`/`ssh_run_opts`, `mkdir_remote`, `write_remote_file` atomique, `chmod_remote`, `test_connection`, `sh_quote`. `scp` est un stub. |
| `remote/deploy.rs` + `remote/templates/{deploy,hook}.txt` | Génération du hook `post-receive` et de `deploy.py` par substitution de jetons (`include_str!` + `.replace`). Constantes `TERMINAL_EXECUTION_STATUSES` / `PENDING_EXECUTION_STATUSES`, `REMOTE_STATUS_CAPABILITY`, `GENERATED_MARKER`. |
| `workspaces/manager.rs` | Contrôleur central : CRUD, cycle `start`/`stop`/`stop_with_sync`, réconciliation, états, import/export, sync git, CI, `publish`, supervision read-only (`server_health`, `server_logs`, `server_deploy_status`, `server_execution_status`), `ensure_running` qui interroge Docker (`live_state`). |
| `workspaces/status.rs` | Ce qu'une carte affiche et les actions offertes : `StatusTone`, `WorkspaceAction`, `CardAction`, `WorkspaceStatus`, `status_for`, `can_*`, `primary_action`, `card_actions`, `summary_line`. Aucun widget. |
| `workspaces/close.rs` | `CloseSequence` : fermeture ordonnée (réconcile → export → sync git → stop) avec chaque décision injectée dans `CloseHooks`. Définie mais non appelée aujourd'hui. |
| `workspaces/dialogs.rs` | Ce que les formulaires collectent (`CreatePlan`) et `fresh_managed_db_config`. |
| `workspaces/ci.rs` | CI du repo du workspace : `github_repo_path`, `collect_workflows`, `load_export`, `missing_credentials`, `workflow_eligibility`, `save_ci_selection`, `render_harness()` (YAML + `validate.py`/`runner.py` générés). |

`tests/docker_integration.rs` couvre le daemon Docker, le rendu Compose et les
commandes de statut d'exécution générées ; les tests s'auto-ignorent sans daemon.

---

## Faits mesurés sur un vrai hôte (ne pas re-dériver par approximation)

- **`/healthz` est liveness, pas readiness.** Sur n8n 2.40, la liveness passe au
  vert ~2 s avant que le gestionnaire d'interface soit monté ; dans cette
  fenêtre `GET /` répond `Cannot GET /`. `n8n/owner.rs::wait_for_n8n` sonde donc
  `/healthz/readiness` et retombe sur la racine de l'UI si la route répond 404
  (versions antérieures).
- **L'API publique est schema-strict.** `POST /api/v1/workflows` valide avec
  `additionalProperties: false` : un champ réservé au serveur présent dans un
  export (`description`, `active`, `triggerCount`, `shared`, …) donne HTTP 400.
  Seule la whitelist `n8n/exports.rs::WORKFLOW_KEYS` (et son équivalent dans le
  `deploy.py` généré) survit à l'envoi.
- **Les `/rest/*` s'authentifient par le cookie de session `n8n-auth`** issu de
  `POST /rest/login` ; n8n 2.33+ ne renvoie plus de token dans le corps et
  `Authorization: Bearer <JWT>` est refusé. `owner.rs` documente explicitement
  ce contrat et s'appuie sur le `Client` reqwest pour conserver le cookie (voir
  lacune : le feature `cookies` n'est pas activé dans `Cargo.toml`).
- **Bootstrap du propriétaire par les endpoints internes**, pas par les variables
  `N8N_INSTANCE_OWNER_*` : `/rest/owner/setup`, `/rest/login`, `/rest/api-keys`
  (avec un tableau `scopes` et un `expiresAt` numérique). n8n sert du HTML
  transitoire au démarrage, d'où les retries de `_ensure_owner`.
- **Pas de réutilisation de fenêtre navigateur dans le port Rust.** Il existe un
  `browser_profile_dir()` dans `core/paths.rs`, mais aucun module `gui/browser.rs`
  n'interroge DevTools `/json/list` ni n'appelle `/json/activate/<id>`. Ouvrir
  deux fois le même workspace ouvrira deux fenêtres.
- **Les labels `docker ps` ne sont pas toujours une map.**
  `docker/manager.rs::parse_container_labels` accepte trois formes : objet JSON,
  chaîne JSON, et chaîne plate `k=v,k=v` émise par Compose 2.35 sur Docker
  Desktop. `list_project_states()` s'en sert pour retrouver projet/service.
- **macOS : les apps lancées par bundle reçoivent un `PATH` minimal.**
  `resolve_docker_command()` sonde `/opt/homebrew/bin`, `/opt/homebrew/sbin`,
  `/usr/local/bin`, `/usr/local/sbin` et
  `/Applications/Docker.app/Contents/Resources/bin` avant de parcourir `PATH`.
- **Config absente et config illisible sont deux branches distinctes.**
  `ConfigStore::load()` renvoie `NotFound` si le fichier n'existe pas ; sinon une
  erreur `Invalid` déclenche `backup_unreadable` dans `main.rs` et l'arrêt. Le
  discriminateur est `store.path().exists()` : un wizard ne doit jamais
  s'ouvrir sur une config qu'on n'a pas pu lire. Un `config.json` legacy est
  migré une seule fois quand la base est absente. La base et ses sidecars
  `-wal`/`-shm` sont chmoddés `0600`.
- **Activation best effort, via la session owner.** L'API publique est refusée
  par n8n 2.x pour un workflow que le propriétaire de la clé ne peut pas
  activer, et `/rest/workflows/<id>/activate` exige le `versionId` courant.
  `deploy.py` relogin après la création de clé (qui révoque la session), réessaie
  la lookup, retombe sur l'API publique, et ne journalise qu'un refus —
  l'import a déjà réussi.
- **`finished` est dérivé, jamais envoyé.** `/rest/executions` n'expose pas ce
  drapeau sur n8n 2.40. `_execution_finished()` dans `templates/deploy.txt`
  privilégie un booléen explicite, puis
  `TERMINAL_EXECUTION_STATUSES` ⇒ `True`, `PENDING_EXECUTION_STATUSES` ⇒ `False`,
  sinon `None`. Les deux jeux vivent dans `remote/deploy.rs` et sont injectés
  dans le script par `__TERMINAL_STATUSES__` / `__PENDING_STATUSES__`. Les
  appels trient sur `startedAt` (plus récent d'abord) ; l'ordre distant n'est
  pas un contrat.
- **Publication = `dev → main` avec un hook synchrone.** `publish()` pousse avec
  un timeout de 300 s (tous les autres appels git ont 30 s) parce que le
  `post-receive` fait `docker compose up` et l'import dans la foulée. Le hook
  débute par `cd "$HOME"` et écrit `last-deploy.json` atomiquement.

---

## Lacunes connues et vérifiées

Ne pas les présenter comme des fonctionnalités terminées. Elles sont explicitement
conservées ici pour que la documentation ne promette pas plus que le binaire.

1. **Vue iced minimale.** `app.rs::update` ne fait que poser `state.status`
   (`"Démarrage de …"`, `"Arrêt de …"`, etc.) et renvoie `Task::none()` : aucun
   appel à `WorkspaceManager` depuis un bouton. `view` affiche un titre, un
   bouton *Nouveau*, une liste de boutons workspace et le statut ; pas de cartes,
   pas de menu contextuel, pas de pilule d'état. `workspaces/status.rs` n'est
   pas consommé par la vue. `Message::Tick` ne quitte pas réellement la boucle ;
   `window()` fixe 1024×640 minimum.
2. **Premier lancement.** `prompt_first_launch()` vérifie l'affichage puis
   renvoie `Ok(false)` sans rien écrire (`let _ = (store, docker)`). Les règles
   toolkit-free existent dans `core/first_launch.rs::run_first_launch`, mais
   aucun appelant ne les utilise.
3. **Token GitHub.** `github/auth.rs::resolve_github_token` renvoie toujours
   `None`. `git/manager.rs::git_seed_remote` (le seul chemin qui insère un token
   dans une URL jetable, sans `-u`) n'a aucun appelant. Le champ `github_token`
   de `ConfigStore` reste lui persisté dans la table `meta` (héritage du schéma).
4. **SCP.** `remote/ssh.rs::scp` renvoie `Err("Not implemented")`. Les écritures
   distantes passent par `ssh_run_opts` + `cat > …tmp && mv -f`.
5. **Journal.** `monitoring/bootstrap.rs::capture_exceptions` est un no-op et
   aucun layer `tracing_subscriber` n'écrit dans `EventStore`. Les deux
   événements de session (`Surveillance active` / `Surveillance terminée`) sont
   émis avec `tracing::info!` uniquement : la paire et l'ordre existent dans le
   code, mais elle n'est pas consultable via `search_events`.
6. **Templates.** `core/templates.rs::read_template` n'est appelé nulle part et
   construit `<CARGO_MANIFEST_DIR>/templates/<package>/<name>`, un dossier qui
   n'existe pas. Les templates réels sont `include_str!` dans `remote/deploy.rs`
   et les chaînes brutes de `workspaces/ci.rs`.
7. **Scopes divergents.** `n8n/scopes.rs` porte encore 11 scopes legacy
   (`workflow:list`, `workflow:create`, …) alors que `remote/deploy.rs` en porte
   6 autres (`workflow:read`, `workflow:write`, `workflow:execute`, …) injectés
   dans `deploy.py`. Le commentaire de `n8n/scopes.rs` affirme qu'ils doivent
   être identiques : ce n'est pas le cas.
8. **Divers.** `database/mod.rs::has_db_layout` est un stub qui renvoie toujours
   `false` (la vraie détection est dans `database/layout.rs`) ; `CloseSequence`
   n'a pas d'appelant (`app.rs` utilise directement `stop_with_sync`) ;
   `ConfigStore::close()` est un no-op.
9. **Distribution.** `scripts/build.sh` fait `cargo build --release` puis copie
   le binaire dans `dist/`. Il n'y a ni `.dmg`, ni `.AppImage`, ni signature,
   ni `--onefile`/onedir à gérer. Le smoke test `--self-test` est le seul garde-
   fou de l'artefact. Aucun gate `cargo fmt --check` / `cargo clippy` n'est
   configuré en CI.
