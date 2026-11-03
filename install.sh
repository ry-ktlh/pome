#!/bin/sh
# User-local install. Set POME_PREFIX to change the default ~/.local prefix.
set -eu
if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Usage: [POME_PREFIX=/absolute/prefix] sh install.sh' >&2
    exit 2
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 - "$script_dir" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

source = Path(sys.argv[1])
prefix = Path(os.environ.get("POME_PREFIX", "~/.local")).expanduser().absolute()
if ":" in str(prefix) or "\n" in str(prefix):
    sys.exit("POME_PREFIX must not contain a colon or newline (used in PATH).")
manifest = prefix / "share/pome/install-manifest.json"
launcher = b'''#!/bin/sh
# pome writing launcher: no mode switching, one manuscript at a time.
if [ "$#" -gt 1 ]; then
    printf '%s\\n' 'Usage: write [manuscript.txt]' >&2
    exit 2
fi
pome_bin=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$pome_bin/pome" "$@"
'''
files = {
    "bin/pome": ((source / "pome.py").read_bytes(), 0o755),
    "bin/write": (launcher, 0o755),
    "share/pome/writing-env.sh": ((source / "writing-env.sh").read_bytes(), 0o644),
}

def digest(data):
    return hashlib.sha256(data).hexdigest()

old = {}
if manifest.is_symlink():
    sys.exit("Refusing to replace symlink: " + str(manifest))
if manifest.exists():
    try:
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if saved.get("installer") != "pome-writing-environment-v1":
            raise ValueError("not a pome install manifest")
        old = saved["files"]
        if not isinstance(old, dict):
            raise ValueError("invalid file manifest")
    except (OSError, ValueError, KeyError) as exc:
        sys.exit("Cannot read install manifest: " + str(exc))

# Check every target before writing any file. Never replace unknown files or
# local edits, including symlinks. A manifest permits upgrades of our own files.
for name, (data, mode) in files.items():
    target = prefix / name
    if target.is_symlink() or (target.exists() and not target.is_file()):
        sys.exit("Refusing to replace existing target: " + str(target))
    if target.exists():
        existing = digest(target.read_bytes())
        if existing not in (digest(data), old.get(name)):
            sys.exit("Existing file was not installed by pome or has local edits: "
                     + str(target) + "\nMove it aside or select another POME_PREFIX.")

def write_atomic(target, data, mode):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pome-install-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

for name, (data, mode) in files.items():
    write_atomic(prefix / name, data, mode)
record = {"installer": "pome-writing-environment-v1",
          "files": {name: digest(data) for name, (data, _) in files.items()}}
write_atomic(manifest, (json.dumps(record, indent=2) + "\n").encode(), 0o644)
print("Installed pome and write in " + str(prefix / "bin"))
print("Activate in the current shell:")
if "POME_PREFIX" in os.environ:
    print("  export POME_PREFIX=" + shlex.quote(str(prefix)))
print("  . " + shlex.quote(str(prefix / "share/pome/writing-env.sh")))
print("Example: write manuscript.txt")
PY
