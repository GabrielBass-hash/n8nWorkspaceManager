"""Core foundation: domain models, the SQLite config store, platform paths.

Entry: :class:`~n8n_launcher.core.config.ConfigStore` (``config.py``),
:class:`~n8n_launcher.core.filelock.FileLock` / ``acquire_single_instance_lock``
(``filelock.py``), :func:`~n8n_launcher.core.templates.read_template`
(``templates.py``).
Gotcha: an unreadable config is backed up and stops the launcher, an absent
one opens the wizard — two different branches, see ``__main__.main``.
Map: ``docs/architecture.md``.
"""
