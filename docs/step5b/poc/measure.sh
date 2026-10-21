#!/bin/sh
set -u

WORK_DIR=/work
STDOUT_FILE=$WORK_DIR/stdout.txt
STDERR_FILE=$WORK_DIR/stderr.txt
PEAK_RSS_FILE=$WORK_DIR/peak_rss_kib.txt
STOP_FILE=$WORK_DIR/.rss-sampler-stop
CGROUP_DIR=/sys/fs/cgroup
WRAPPER_PID=$$

cd "$WORK_DIR" || exit 125
: >"$STDOUT_FILE"
: >"$STDERR_FILE"
: >"$PEAK_RSS_FILE"
rm -f "$STOP_FILE"

# Best effort: sum container-wide RSS while excluding the wrapper and the short-lived
# ps/awk/sleep sampling helpers. The background sampler shell may remain in the sum.
sample_rss() {
    peak=0
    while :; do
        current=$(ps -e -o pid=,rss=,comm= 2>/dev/null \
            | awk -v wrapper="$WRAPPER_PID" '
                $1 != wrapper && $3 !~ /^(ps|awk|sleep)$/ { total += $2 }
                END { printf "%.0f\n", total + 0 }
            ')
        if [ "$current" -gt "$peak" ] 2>/dev/null; then
            peak=$current
        fi
        if [ -e "$STOP_FILE" ]; then
            break
        fi
        sleep 0.05
    done
    printf '%s\n' "$peak" >"$PEAK_RSS_FILE"
}

start_ns=$(date +%s%N)
sample_rss &
sampler_pid=$!

if [ "$#" -eq 0 ]; then
    printf '%s\n' 'measure.sh requires a command argv' >"$STDERR_FILE"
    command_exit=2
else
    {
        (
            # POSIX /bin/sh (dash on Ubuntu 22.04) defines -f in 512-byte blocks:
            # 16,384 blocks = 8 MiB for each redirected regular output file.
            ulimit -c 0
            ulimit -f 16384
            timeout -k 5s 180s "$@"
        )
        command_exit=$?
    } >"$STDOUT_FILE" 2>"$STDERR_FILE"
fi

end_ns=$(date +%s%N)
: >"$STOP_FILE"
wait "$sampler_pid"
duration_ns=$((end_ns - start_ns))

case $(cat "$PEAK_RSS_FILE" 2>/dev/null) in
    ''|*[!0-9]*) peak_rss_kib=0 ;;
    *) peak_rss_kib=$(cat "$PEAK_RSS_FILE") ;;
esac

read_number() {
    if [ -r "$1" ]; then
        value=$(cat "$1")
        case $value in
            ''|*[!0-9]*) printf 'null' ;;
            *) printf '%s' "$value" ;;
        esac
    else
        printf 'null'
    fi
}

read_key_values() {
    if [ -r "$1" ]; then
        awk '
            BEGIN { printf "{"; separator = "" }
            /^[A-Za-z0-9_.-]+[[:space:]]+[0-9]+$/ {
                printf "%s\"%s\":%s", separator, $1, $2
                separator = ","
            }
            END { printf "}" }
        ' "$1"
    else
        printf 'null'
    fi
}

memory_peak=$(read_number "$CGROUP_DIR/memory.peak")
memory_events=$(read_key_values "$CGROUP_DIR/memory.events")
cpu_stat=$(read_key_values "$CGROUP_DIR/cpu.stat")

cat >"$WORK_DIR/metrics.json" <<EOF
{
  "command_exit": $command_exit,
  "start_ns": $start_ns,
  "end_ns": $end_ns,
  "duration_ns": $duration_ns,
  "inner_timeout_seconds": 180,
  "stdout_stderr_file_limit": {
    "value": 16384,
    "unit": "POSIX 512-byte blocks",
    "bytes_per_file": 8388608
  },
  "peak_process_tree_rss_kib": $peak_rss_kib,
  "rss_sampling": {
    "interval_seconds": 0.05,
    "scope": "all visible container processes",
    "exclusions": "wrapper PID and ps/awk/sleep helpers; sampler shell may be included"
  },
  "cgroup_memory_peak_bytes": $memory_peak,
  "cgroup_memory_peak_scope": "since cgroup creation for the whole stage container; includes wrapper and non-RSS memory such as page cache",
  "cgroup_memory_events": $memory_events,
  "cgroup_cpu_stat": $cpu_stat
}
EOF

rm -f "$STOP_FILE"
exit "$command_exit"
