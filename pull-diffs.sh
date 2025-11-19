#!/usr/bin/bash

usage() {
  echo "Usage: $0 <TARGET> <REMOTE_PATH> <TARBALL_NAME>"
  echo ""
  echo "Arguments:"
  echo "  TARGET         Codespace name (e.g., 'nice-space-name') or SSH host (e.g., ubuntu@1.2.3.4)"
  echo "  REMOTE_PATH    Remote repository root directory"
  echo "  TARBALL_NAME   Name for the .tar.gz archive (e.g., 'changes.tar.gz')"
  echo ""
  echo "Examples:"
  echo "  $0 sturdy-space-abc123 /workspaces/myrepo changes.tar.gz"
  echo "  $0 ubuntu@1.2.3.4 /var/www/myrepo changes.tar.gz"
  exit 1
}

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  usage
fi

if [ $# -ne 3 ]; then
  echo "❌ Error: Invalid number of arguments."
  usage
fi

TARGET="$1"
REMOTE_PATH="$2"
TARBALL="$3"
LOCAL_PATH="."

echo "TARGET       - $TARGET"
echo "REMOTE_PATH  - $REMOTE_PATH"
echo "TARBALL      - $TARBALL"
echo "LOCAL_PATH   - $LOCAL_PATH"

##############################################
# Detect if TARGET is a Codespace or SSH host
##############################################

# Codespace rule: has "-" and does NOT contain "@"
if [[ "$TARGET" == *"-"* && ! "$TARGET" =~ @ ]]; then
  IS_CODESPACE=true
else
  IS_CODESPACE=false
fi

##############################################
# Remote: Collect modified + untracked files
##############################################

if [[ "$IS_CODESPACE" == true ]]; then
  echo "📦 Collecting diffs inside Codespace..."

  gh codespace ssh -c "$TARGET" -- "
    cd '$REMOTE_PATH' || exit 1
    FILES=\$(git ls-files -m -o --exclude-standard | grep -v -F -f <(git ls-files -d))
    echo \"Modified & untracked files: \$FILES\"
    if [ -z \"\$FILES\" ]; then
      echo '⚠️ No modified or untracked files found.'
      exit 0
    fi
    echo \"\$FILES\" | tar --exclude=\".gitignore\" -czf /tmp/$TARBALL -T -
  "

  echo "📥 Copying tarball from Codespace..."
  gh codespace cp remote:/tmp/$TARBALL "$LOCAL_PATH/" -c "$TARGET"

  echo "🧹 Cleaning up remote tarball..."
  gh codespace ssh -c "$TARGET" -- "rm -f /tmp/$TARBALL"

else
  echo "🌐 Collecting diffs via SSH..."

  ssh "$TARGET" "
    cd '$REMOTE_PATH' || exit 1
    FILES=\$(git ls-files -m -o --exclude-standard | grep -v -F -f <(git ls-files -d))
    echo \"Modified & untracked files: \$FILES\"
    if [ -z \"\$FILES\" ]; then
      echo '⚠️ No modified or untracked files found.'
      exit 0
    fi
    echo \"\$FILES\" | tar --exclude=\".gitignore\" --exclude=\"package-lock.json\" --exclude=\"pnpm-lock.yaml\" -czf /tmp/$TARBALL -T -
    git diff src/infrastructure/notification/services/notification-delivery.service.ts
  "

  echo "📥 Copying tarball from SSH host..."
  scp "$TARGET:/tmp/$TARBALL" "$LOCAL_PATH/"

  echo "🧹 Cleaning up remote tarball..."
  ssh "$TARGET" "rm -f /tmp/$TARBALL"
fi

##############################################
# Extract locally
##############################################

echo "📂 Extracting archive locally..."
tar --touch -xzf "$LOCAL_PATH/$TARBALL" -C "$LOCAL_PATH"

echo "🧹 Cleaning up local tarball..."
rm -f "$LOCAL_PATH/$TARBALL"

echo "✅ Pull of modified & untracked files completed successfully."
