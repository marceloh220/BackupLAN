#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TEST_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/etc/nas-backup" "$TEST_DIR/etc/systemd/system" "$TEST_DIR/bin" "$TEST_DIR/source folder" "$TEST_DIR/destination"
sed -n "/^  cat > \/usr\/local\/sbin\/nas-backup <<'BACKUP_SCRIPT'/,/^BACKUP_SCRIPT$/p" "$ROOT/nas-setup.sh" |
  sed '1d;$d' |
  sed -e "s|/etc/nas-backup|$TEST_DIR/etc/nas-backup|g" \
      -e "s|/etc/systemd/system|$TEST_DIR/etc/systemd/system|g" \
      -e "s|/tmp/nas-backup-files|$TEST_DIR/nas-backup-files|g" \
      -e '/^\[\[ $EUID -eq 0 \]\]/d' > "$TEST_DIR/nas-backup"
bash -n "$TEST_DIR/nas-backup"
eval "$(sed -n '/^human_size() {$/,/^}$/p' "$TEST_DIR/nas-backup")"
[[ $(human_size 0) == '0 bytes' ]]
[[ $(human_size 999) == '999 bytes' ]]
[[ $(human_size 1000) == '1.00KB' ]]
[[ $(human_size 1500) == '1.50KB' ]]
[[ $(human_size 1000000) == '1.00MB' ]]
[[ $(human_size 1500000000) == '1.50GB' ]]
[[ $(human_size 1000000000000) == '1.00TB' ]]
printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$TEST_DIR/systemctl.log"\n' > "$TEST_DIR/bin/systemctl"
printf '#!/usr/bin/env bash\nif [[ ${MOCK_NO_SPACE:-no} == yes ]]; then printf "Avail\\n0\\n"; else /usr/bin/df "$@"; fi\n' > "$TEST_DIR/bin/df"
printf '#!/usr/bin/env bash\nif [[ ${MOCK_TAR_FAILURE:-no} == yes && ${1:-} == -cf && ${2:-} != - ]]; then printf "partial" > "$2"; exit 2; fi\n/usr/bin/tar "$@"\n' > "$TEST_DIR/bin/tar"
chmod +x "$TEST_DIR/bin/systemctl" "$TEST_DIR/bin/df" "$TEST_DIR/bin/tar"
if ! command -v flock >/dev/null; then
  printf '#!/usr/bin/env bash\nexit 0\n' > "$TEST_DIR/bin/flock"
  chmod +x "$TEST_DIR/bin/flock"
fi
export TEST_DIR
export PATH="$TEST_DIR/bin:$PATH"
{
  printf 'BACKUP_DIR=%q\nBACKUP_LOG=%q\n' "$TEST_DIR/destination" "$TEST_DIR/backup.log"
  printf 'BACKUP_RETENTION_DAYS=30\nBACKUP_PERIOD=weekly\nBACKUP_COMPRESSION=gzip\n'
  printf 'BACKUP_SOURCE=( %q )\nBACKUP_EXCLUDE_TYPES=( "*.tmp" )\n' "$TEST_DIR/source folder"
} > "$TEST_DIR/etc/nas-backup/config"
backup() { bash "$TEST_DIR/nas-backup" "$@"; }
printf 'keep me\n' > "$TEST_DIR/source folder/document.txt"
printf 'skip me\n' > "$TEST_DIR/source folder/excluded.tmp"
mkdir "$TEST_DIR/source folder/nested"
printf 'nested\n' > "$TEST_DIR/source folder/nested/data.txt"
backup configure
grep -qx 'OnCalendar=weekly' "$TEST_DIR/etc/systemd/system/nas-backup.timer"
[[ $(backup compression) == gzip ]]
for method in gzip bzip2 xz no-compress; do
  backup compression "$method"
  backup run
done
count=$(backup list | wc -l)
[[ "$count" -eq 4 ]]
backup list | grep -q '10.24KB'
for archive in "$TEST_DIR"/destination/nas-backup-*; do
  tar -tf "$archive" > "$TEST_DIR/members"
  grep -q 'source folder/document.txt$' "$TEST_DIR/members"
  grep -q 'source folder/nested/data.txt$' "$TEST_DIR/members"
  if grep -q excluded.tmp "$TEST_DIR/members"; then exit 1; fi
done
backup space > "$TEST_DIR/space-output"
grep -Eq '^Backups: [0-9.]+(bytes|[KMGT]B| bytes) ocupados$' "$TEST_DIR/space-output"
grep -q '^Livre no destino: ' "$TEST_DIR/space-output"
if MOCK_NO_SPACE=yes backup run; then echo 'No-space backup unexpectedly succeeded.' >&2; exit 1; fi
[[ $(backup list | wc -l) -eq 4 ]]
grep -q 'IMPOSSIBILIDADE: espaco insuficiente' "$TEST_DIR/backup.log"
grep -q 'necessario=1.06MB, livre=0 bytes' "$TEST_DIR/backup.log"
[[ $(find "$TEST_DIR/destination" -name '*.tmp' | wc -l) -eq 0 ]]
if MOCK_TAR_FAILURE=yes backup run; then echo 'Failing archive unexpectedly succeeded.' >&2; exit 1; fi
[[ $(backup list | wc -l) -eq 4 ]]
[[ $(find "$TEST_DIR/destination" -name '*.tmp' | wc -l) -eq 0 ]]
grep -q 'FALHA: backup nao criado (codigo 2)' "$TEST_DIR/backup.log"
backup excludes remove '*.tmp'
[[ -z $(backup excludes) ]]
backup excludes add '*.bak'
[[ $(backup excludes) == '*.bak' ]]
backup sources remove "$TEST_DIR/source folder"
[[ -z $(backup sources) ]]
if backup run; then echo 'Empty-source backup unexpectedly succeeded.' >&2; exit 1; fi
backup sources add "$TEST_DIR/source folder"
backup retention 7
[[ $(backup retention) == 7 ]]
for period in daily monthly weekly; do
  backup period "$period"
  grep -qx "OnCalendar=$period" "$TEST_DIR/etc/systemd/system/nas-backup.timer"
done
if backup compression invalid; then exit 1; fi
[[ $(backup compression) == no-compress ]]
if backup retention 0; then exit 1; fi
[[ $(backup retention) == 7 ]]
old_archive=$(find "$TEST_DIR/destination" -name '*.tar.gz' -print -quit)
touch -t 202001010000 "$old_archive"
printf 'unrelated\n' > "$TEST_DIR/destination/other.tar.gz"
touch -t 202001010000 "$TEST_DIR/destination/other.tar.gz"
backup prune
[[ ! -f "$old_archive" && -f "$TEST_DIR/destination/other.tar.gz" ]]
backup destination "$TEST_DIR/source folder/backups"
backup run
new_archive=$(find "$TEST_DIR/source folder/backups" -name '*.tar' -print -quit)
tar -tf "$new_archive" > "$TEST_DIR/members"
if grep -q 'backups/' "$TEST_DIR/members"; then echo 'Destination included in backup.' >&2; exit 1; fi
if backup sources add "$TEST_DIR/source folder/backups"; then exit 1; fi
backup logs > "$TEST_DIR/log-copy"
grep -q SUCESSO "$TEST_DIR/log-copy"
grep -q REMOVIDO "$TEST_DIR/log-copy"
printf 'PASS: formats, filters, empty lists, space refusal, partial cleanup, retention, periods, destination, logs.\n'