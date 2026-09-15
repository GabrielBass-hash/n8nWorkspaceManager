# TODOs — retours de l'audit de code

Suivi des bugs identifiés lors de l'état des lieux du 2026-09-14 (103 tests unitaires verts).
Chaque entrée suit le schéma : `- [ ] description` → `- [x] description` quand corrigé.
Les vérifications se font avec `pytest` (unit) et `pytest -m integration` (Docker requis).

## Critiques / visibles

- [x] **1. Reconstruction de toute la liste toutes les 5 s (flickering)**
  `_poll_states` → `_run_async(reconcile_all)` poste `refresh` à chaque cycle.
  - `gui.py:433-440` — `_poll_states`
  - `gui.py:659-673` — `_run_async` (pose `on_success or self.refresh`)
  - **Corrigé** : `_poll_states` ne poste `refresh` que si `reconcile_all()` renvoie > 0, et garde `_poll_in_flight` pour éviter d'empiler des cycles pendant un `docker compose ps` long. Tests ajoutés : `test_poll_skips_refresh_when_state_unchanged`, `test_poll_refreshes_only_when_state_changed`, `test_poll_skips_overlapping_reconcile`.

- [x] **2. Timeout Docker 30 s → échec du 1er démarrage**
  `DockerManager()` par défaut à 30 s ; `compose up -d` dépasse pendant le pull de l'image n8n.
  - `docker_manager.py:54` — `timeout=30.0` par défaut
  - Tests d'intégration OK car surchar gent à 180 s : `tests/integration/conftest.py:8`
  - **Corrigé** : `up()` dispose d'un timeout dédié `UP_TIMEOUT = 600s` (pulls d'image) transmis via `_compose(..., timeout=)` ; les commandes rapides gardent le timeout par défaut. Tests ajoutés : `test_up_uses_extended_timeout_for_image_pulls`, `test_up_accepts_custom_timeout`, `test_fast_commands_keep_default_timeout`.

- [x] **3. Fermeture lente (double `docker down`)**
  `on_close` arrête tout, puis `atexit stop_all` répète l'arrêt ; plus `reconcile_all` synchrone dans `on_close`.
  - `__main__.py:23-45` — `stop_all` / enregistrement `atexit`
  - `gui.py:691-705` — `on_close` ; `gui.py:735-746` — `_close_stop`
  - **Corrigé** : `WorkspaceManager.stop` devient idempotent — `docker down` ne s'exécute que si `compose.exists()` ET `workspace.state is not STOPPED`. Le `stop_all` d'`atexit` après un `on_close` passé skip le down pour chaque workspace. Test ajouté : `test_stop_is_idempotent_on_already_stopped`.

- [x] **4. « + » central non cliquable + clic dans le vide = dialogue de création**
  - `gui.py:219` — `<Button-1>` sur `workspace_list` → `prompt_create_workflow`
  - `gui.py:221-233` — watermark (Label enfant, aucune action)
  - **Corrigé** : `<Button-1>` sur `workspace_list` déclenche `prompt_create_workflow` uniquement si la liste est vide ; sinon désélectionne. Le watermark « + » reçoit son propre binding `<Button-1>` → création. Tests ajoutés : `test_empty_space_click_deselects_when_rows_exist`, `test_watermark_click_triggers_creation`.

## Secondaires

- [ ] **5. Mutations de l'état Tk depuis des worker threads**
  - `gui.py:532` — `self._launching = None` dans `action()`
  - `gui.py:655` — `self._selected_id = None` dans `_delete_after_confirm`
  - Fix attendu : toute mutation d'attribut UI passe par la file d'événements `self.events`.

- [ ] **6. Raccourci Windows cassé (`.url` ne lance pas l'app)**
  - `shortcuts.py:44-47`
  - Fix attendu : générer un vrai `.lnk` (via `win32com`/`pywin32` ou batch de lancement), ou à défaut un `.bat` → `.desktop` Linux et `.command` Mac restent inchangés.

- [ ] **7. Clé API n8n régénérée à chaque démarrage**
  - `owner_setup.py:77-84` — `_remove_launcher_keys` supprime puis recrée la clé
  - `workspace_manager.py:204-221` — `_ensure_db_credentials` re-bootstrap sur 401/403
  - Fix attendu : réutiliser la clé existante tant qu'elle est valide ; `_request` = GET/DELETE sans body JSON (`owner_setup.py:119-123`).

## Hygiène / design

- [ ] **8. `ConfigStore` sans verrou → écritures concurrentes**
  - `config.py:31-47` — `save()` (temp→rename)
  - `workspace_manager.py:71-87` — `reconcile_all`
  - Fix attendu : verrou (`threading.Lock`) autour de load/save.

- [ ] **9. `restart_required` jamais consommé**
  - `workspace_manager.py:135` — posé par `update`, lu nulle part
  - Fix attendu : l'utiliser (badge UI « redémarrage requis ») ou le supprimer.

- [ ] **10. Suppression = fuite disque (volumes + compose laissés)**
  - `workspace_manager.py:140-146` — `delete`
  - Fix attendu : nettoyer `config_dir/workspaces/<id>/` et les volumes `n8ndata-<id>`/`pgdata-<id>` (option explicitement confirmée dans l'UI).

- [ ] **11. Secrets en clair dans `config.json`**
  - `config.py`, `models.py:81-103` (owner_email/password, api_key, passwords DB)
  - Fix attendu (à évaluer) : chiffrement symétrique clé locale, ou au minimum alerte à l'installation.

- [ ] **12. Dossier avec `db/schema.sql` force la base locale**
  - `gui.py:475-476` — `db_config_for_folder` → MANAGED, dialogue sauté
  - Fix attendu : proposer le choix (locale / distante / aucune) même si un schéma existe.

---

## Vérification finale

- [ ] `pytest` (suite unitaire) vert
- [ ] `pytest -m integration` vert (Docker requis) — surtout après les items 1, 2, 3
- [ ] Lancement `./dist/n8n-launcher` : + cliquable, pas de flickering, fermeture rapide