# SSH server fixture for tests/integration/test_remote_deploy.py.
#
# Exercises the real remote-deployment flow (install_server / publish /
# post-receive hook / deploy.py) end to end against an actual sshd. The
# container runs sshd as root with key-only login, so the docker CLI inside it
# can drive a bind-mounted host docker socket (Phase B) without any
# group/uid gymnastics — the processes the hook spawns simply bypass the
# socket's permission checks.
#
# The image stays key-independent: the test generates an ed25519 keypair per
# session and injects the public half through the AUTHORIZED_KEYS env var.
FROM alpine:3.21

# The generated post-receive hook is a bash script (`#!/usr/bin/env bash`),
# so the fixture ships bash like any real production server would. Port
# forwarding is re-enabled because Alpine's sshd_config ships
# "AllowTcpForwarding no": the generated deploy code talks to the n8n Compose
# published on the host through http://127.0.0.1:<port>, and the test reaches
# that port from inside the sandbox with a reverse tunnel opened by the host.
RUN apk add --no-cache openssh docker-cli docker-cli-compose git python3 bash socat \
    && mkdir -p /run/sshd /root/.ssh /etc/ssh/hostkeys \
    && chmod 700 /root/.ssh \
    && sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/^#*AllowTcpForwarding.*/AllowTcpForwarding yes/' /etc/ssh/sshd_config \
    && echo 'AuthorizedKeysFile .ssh/authorized_keys' >> /etc/ssh/sshd_config

EXPOSE 22

ENV AUTHORIZED_KEYS=""
# Host keys live in /etc/ssh/hostkeys so the test can pin them in a named
# docker volume. macOS ssh ignores $HOME when resolving ~/.ssh/known_hosts
# (it uses the passwd-database home), so a fresh container would collide with
# previously-accepted keys; persistent host keys make every run indifferent
# to that quirk on all platforms. Keys are only generated when missing.
CMD ["sh", "-c", "mkdir -p /etc/ssh/hostkeys; [ -f /etc/ssh/hostkeys/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/hostkeys/ssh_host_ed25519_key; [ -f /etc/ssh/hostkeys/ssh_host_rsa_key ] || ssh-keygen -q -t rsa -b 3072 -N '' -f /etc/ssh/hostkeys/ssh_host_rsa_key; chmod 600 /etc/ssh/hostkeys/*; printf '%s\\n' \"$AUTHORIZED_KEYS\" > /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys; exec /usr/sbin/sshd -D -e -o HostKey=/etc/ssh/hostkeys/ssh_host_ed25519_key -o HostKey=/etc/ssh/hostkeys/ssh_host_rsa_key"]
