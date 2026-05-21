#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./gnome-delete-workspace.sh 2
#   ./gnome-delete-workspace.sh 6
#
# What it does:
#   - leaves dynamic workspaces enabled
#   - deletes the NAME at position N
#   - renumbers remaining named workspaces (shifts down)
#   - stores names in the GNOME workspace-names key as "N-suffix"
#   - does NOT move any windows
#   - with dynamic workspaces on, GNOME may add/remove trailing empty workspaces automatically
#   - does NOT guarantee an empty workspace will remain at N while dynamic workspaces are on
#
# Renumbering examples:
#   - before: ["1-dev", "2-mail", "3-chat"], delete N=2
#     after:  ["1-dev", "2-chat"]
#   - before: ["1-dev", "2-", "3-chat"], delete N=1
#     after:  ["1-", "2-chat"]

POS="${1:?Give the delete position, e.g. 6}"

python3 - "$POS" <<'PY'
import ast
import re
import subprocess
import sys

raw_pos = sys.argv[1].strip()
try:
    pos = int(raw_pos)
except ValueError:
    raise SystemExit("Position must be an integer >= 1")

if pos < 1:
    raise SystemExit("Position must be >= 1")

def gget(schema, key):
    return subprocess.check_output(["gsettings", "get", schema, key], text=True).strip()

def gset(schema, key, value):
    subprocess.run(["gsettings", "set", schema, key, value], check=True)

def parse_workspace_names(raw):
    text = raw.strip()
    # GNOME may prefix array literals with a GVariant type tag like "@as".
    # Strip that prefix so the remaining value is a plain Python literal.
    # This keeps older and newer gsettings outputs compatible.
    text = re.sub(r"^@[^ ]+\s+", "", text, count=1)
    if text == "[]":
        return []
    return ast.literal_eval(text)

raw = gget("org.gnome.desktop.wm.preferences", "workspace-names")
names = parse_workspace_names(raw)

# Keep only explicit custom names like "6-genewise".
# Drop blanks and GNOME placeholders like "Workspace 17"
custom = []
for n in names:
    s = str(n).strip()
    if not s:
        continue
    if re.fullmatch(r"Workspace \d+", s):
        continue
    custom.append(s)

# Parse into numeric prefix and suffix when present.
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

if not suffixes:
    raise SystemExit("No named workspaces to delete")

if pos > len(suffixes):
    raise SystemExit(f"Position {pos} out of bounds (1..{len(suffixes)})")

# Remove the suffix at requested position and renumber remaining entries
del suffixes[pos - 1]

if suffixes:
    renumbered = [f"{i}-{suffix}" if suffix else f"{i}-" for i, suffix in enumerate(suffixes, start=1)]
    gset("org.gnome.desktop.wm.preferences", "workspace-names", repr(renumbered))
else:
    # Clear the GNOME list when no names remain
    gset("org.gnome.desktop.wm.preferences", "workspace-names", repr([]))
    renumbered = []

print("Updated workspace names:")
for n in renumbered:
    print(" ", n)
PY
