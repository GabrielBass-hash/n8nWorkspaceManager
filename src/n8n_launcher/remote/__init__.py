"""Remote server deployment: SSH plumbing and the generated server listener.

The launcher deploys a workspace to a production server through git: work
lives on the workspace's ``dev`` branch, publishing pushes ``dev:main`` to a
bare repository hosted on the server (the server's ``main`` is the production
reference, never created on GitHub), and the server's ``post-receive`` hook
redeploys the stack (see ``deploy.py``). This package only communicates over
the SSH key configured in ``ServerConfig`` — passphrases are left to the
user's SSH agent.
"""

from .deploy import (
    bare_dir,
    build_secrets_document,
    marker_path,
    render_deploy_script,
    render_hook,
    resolve_base,
    server_remote_url,
)
from .ssh import (
    SshError,
    chmod_remote,
    mkdir_remote,
    ssh_run,
    test_connection,
    write_remote_file,
)

__all__ = [
    "SshError",
    "bare_dir",
    "build_secrets_document",
    "chmod_remote",
    "marker_path",
    "mkdir_remote",
    "render_deploy_script",
    "render_hook",
    "resolve_base",
    "server_remote_url",
    "ssh_run",
    "test_connection",
    "write_remote_file",
]
