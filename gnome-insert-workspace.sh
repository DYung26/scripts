#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./insert-workspace-name.sh 6
#   ./insert-workspace-name.sh 6 inbox
#
# What it does:
#   - assumes static workspaces (org.gnome.desktop.wm.preferences num-workspaces)
#   - inserts a new NAME at position N
#   - renumbers all later named workspaces
#   - stores names in the GNOME workspace-names key as "N-suffix"
#   - increases num-workspaces by 1 so the inserted workspace actually exists
#   - does NOT move any windows

POS="${1:?Give the insert position, e.g. 6}"
LABEL="${2:-}"

python3 - "$POS" "$LABEL" <<'PY'
import ast
import re
import subprocess
import sys

pos = int(sys.argv[1])
label = sys.argv[2].strip()

if pos < 1:
    raise SystemExit("Position must be >= 1")

def gget(schema, key):
    return subprocess.check_output(
        ["gsettings", "get", schema, key],
        text=True
    ).strip()

def gset(schema, key, value):
    subprocess.run(
        ["gsettings", "set", schema, key, value],
        check=True
    )

raw = gget("org.gnome.desktop.wm.preferences", "workspace-names")
names = ast.literal_eval(raw)

# Keep only explicit custom names like "6-genewise".
# GNOME may also return placeholder entries such as "Workspace 17"; those are
# not part of the durable naming scheme and are ignored.
# Drop blanks and GNOME placeholders like "Workspace 17"
custom = []
for n in names:
    s = str(n).strip()
    if not s:
        continue
    if re.fullmatch(r"Workspace \d+", s):
        continue
    custom.append(s)

# Split existing names into numeric prefix + suffix when possible.
# Backward compatibility: unnumbered entries are preserved as suffixes and
# renumbered later, while numbered entries keep only their suffix payload.
parsed = []
for name in custom:
    m = re.match(r"^(\d+)-(.*)$", name)
    if m:
        parsed.append((int(m.group(1)), m.group(2)))
    else:
        parsed.append((None, name))

# Sort numbered entries by their current number, keep unnumbered at the end
numbered = [(n, s) for n, s in parsed if n is not None]
unnumbered = [s for n, s in parsed if n is None]
numbered.sort(key=lambda x: x[0])

suffixes = [s for _, s in numbered]
suffixes.extend(unnumbered)

# Pad if user inserts beyond current named range
while len(suffixes) < pos - 1:
    suffixes.append("")

# Insert the new empty/custom suffix before renumbering so the final list is
# always dense and starts at 1.
suffixes.insert(pos - 1, label)

# Renumber from 1..N; a blank suffix stays represented as "N-".
renumbered = [f"{i}-{suffix}" if suffix else f"{i}-" for i, suffix in enumerate(suffixes, start=1)]

gset("org.gnome.desktop.wm.preferences", "workspace-names", repr(renumbered))

# Static workspaces: bump the total count by 1 so the newly inserted
# workspace actually exists instead of just being a name with nothing
# behind it. Also make sure we never end up with fewer workspaces than
# named entries.
current_total = int(gget("org.gnome.desktop.wm.preferences", "num-workspaces"))
new_total = max(current_total + 1, len(renumbered))
gset("org.gnome.desktop.wm.preferences", "num-workspaces", str(new_total))

print("Updated workspace names:")
for n in renumbered:
    print(" ", n)
print(f"num-workspaces: {current_total} -> {new_total}")
PY
