"""Remote server deployment and bounded observability helpers.

The launcher deploys a workspace to a production server through git and uses
the same SSH connection for read-only health, log, marker and execution-status
queries. Credential-bearing files are never returned by those helpers.
"""

from .deploy import (
    REMOTE_STATUS_CAPABILITY,
    bare_dir,
    build_secrets_document,
    deploy_script_path,
    history_path,
    log_path,
    marker_path,
    render_deploy_script,
    render_hook,
    resolve_base,
    server_remote_url,
)
from .ssh import (
    MAX_REMOTE_HISTORY_ENTRIES,
    MAX_REMOTE_LOG_LINES,
    MAX_REMOTE_OUTPUT_BYTES,
    RemoteExecution,
    RemoteExecutionStatus,
    RemoteHealth,
    SshError,
    chmod_remote,
    mkdir_remote,
    read_remote_deploy_history,
    read_remote_deploy_marker,
    read_remote_file,
    remote_execution_status,
    remote_health,
    remote_logs,
    ssh_run,
    tail_remote_file,
    test_connection,
    write_remote_file,
)

__all__ = [
    "MAX_REMOTE_HISTORY_ENTRIES",
    "MAX_REMOTE_LOG_LINES",
    "MAX_REMOTE_OUTPUT_BYTES",
    "REMOTE_STATUS_CAPABILITY",
    "RemoteExecution",
    "RemoteExecutionStatus",
    "RemoteHealth",
    "SshError",
    "bare_dir",
    "build_secrets_document",
    "chmod_remote",
    "deploy_script_path",
    "history_path",
    "log_path",
    "marker_path",
    "mkdir_remote",
    "read_remote_deploy_history",
    "read_remote_deploy_marker",
    "read_remote_file",
    "remote_execution_status",
    "remote_health",
    "remote_logs",
    "render_deploy_script",
    "render_hook",
    "resolve_base",
    "server_remote_url",
    "ssh_run",
    "tail_remote_file",
    "test_connection",
    "write_remote_file",
]
