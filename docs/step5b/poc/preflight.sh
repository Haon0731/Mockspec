#!/bin/sh
set -eu
/tools/jdk/bin/java -version
/tools/jdk/bin/javac -version
printf 'uid='; id -u
printf 'caps='; sed -n '/^CapEff:/p;/^NoNewPrivs:/p' /proc/self/status
printf 'memory_limit='; cat /sys/fs/cgroup/memory.max
printf 'cpu_limit='; cat /sys/fs/cgroup/cpu.max
printf 'pids_limit='; cat /sys/fs/cgroup/pids.max
printf 'network_routes\n'; cat /proc/net/route
if test -e /home/haon/projects/Stub-Tester; then echo 'FAIL: host repository visible'; exit 3; fi
if touch /input/.write-probe 2>/dev/null; then echo 'FAIL: input writable'; exit 3; fi
if touch /etc/.write-probe 2>/dev/null; then echo 'FAIL: root writable'; exit 3; fi
if test -e /var/run/docker.sock; then echo 'FAIL: docker socket visible'; exit 3; fi
command -v ps
printf 'PASS: host path absent; input/root readonly; no daemon socket\n'
