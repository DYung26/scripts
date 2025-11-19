#!/usr/bin/bash

usage() {
  echo "Usage: $0 <TARGET> <LOCAL_REPO_PATH> <TARBALL_NAME> [REMOTE_PATH]"
  echo ""
  echo "Arguments:"
  echo "  TARGET   Name of the GitHub Codespace (e.g., 'nice-space-name')"
  echo "  LOCAL_REPO_PATH  Local git repository path"
  echo "  TARBALL_NAME     Name for the .tar.gz archive (e.g., 'changes.tar.gz')"
  echo ""
  echo "Example:"
  echo "  $0 sturdy-space-abc123 ./shaare-backend changes.tar.gz"
  echo "  $0 ubuntu@1.2.3.4 ./shaare-backend changes.tar.gz"
  exit 1
}

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  usage
fi

if [ $# -lt 3 ] || [ $# -gt 4 ]; then
  echo "❌ Error: Invalid number of arguments."
  usage
fi

TARGET="$1"
LOCAL_REPO_PATH="$2"
TARBALL="$3"
REMOTE_PATH="${4:-}"

# echo "CODESPACE_NAME - $CODESPACE_NAME"
echo "TARGET - $TARGET"
echo "LOCAL_REPO_PATH - $LOCAL_REPO_PATH"
echo "TARBALL - $TARBALL"
echo "REMOTE_PATH - $REMOTE_PATH"
echo "$(basename $LOCAL_REPO_PATH)"

if [ ! -d "$LOCAL_REPO_PATH/.git" ]; then
  echo "❌ Error: $LOCAL_REPO_PATH is not a git repository"
  exit 1
fi

echo "📦 Collecting modified + untracked files..."

cd "$LOCAL_REPO_PATH" || exit 1

# Get modified and untracked files
FILES=$(git ls-files -m -o --exclude-standard | grep -v -F -f <(git ls-files -d))
echo "Untracked Files: $FILES"

if [ -z "$FILES" ]; then
echo "✅ No modified or untracked files to upload."
exit 0
fi

echo "$FILES" | tar --exclude=".gitignore" --exclude="package-lock.json" --exclude="pnpm-lock.yaml" -czf "$TARBALL" -T -

if [[ "$TARGET" == *"-"* && ! "$TARGET" =~ @ ]]; then
  echo "📤 Uploading tarball to Codespace..."
  REMOTE_PATH="${REMOTE_PATH:-/workspaces}"
  gh codespace cp "$TARBALL" remote:$REMOTE_PATH/ -c "$TARGET"
  
  echo "📂 Extracting in Codespace..."
  gh codespace ssh -c "$TARGET" -- "
  	cd $REMOTE_PATH/$(basename $LOCAL_REPO_PATH) &&
	git checkout . &&
	{ [ -f \"../$(basename $LOCAL_REPO_PATH)-gitignore\" ] && {
		\"echo gitignore exists\" cp .gitignore .gitignore.old && cp gitignore .gitignore; 
	}; } || true &&
	tar --touch -xzf $REMOTE_PATH/$TARBALL -C $REMOTE_PATH/$(basename $LOCAL_REPO_PATH) &&
	rm $REMOTE_PATH/$TARBALL
  "
else
  echo "🌐 Detected normal SSH host: $TARGET"
  REMOTE_PATH="${REMOTE_PATH:-~}"
  scp "$TARBALL" "$TARGET:$REMOTE_PATH/"
  ssh "$TARGET" "
    cd $REMOTE_PATH/$(basename $LOCAL_REPO_PATH) &&
    git checkout . &&
    tar --touch -xzf $REMOTE_PATH/$TARBALL -C $REMOTE_PATH/$(basename $LOCAL_REPO_PATH) &&
    rm $REMOTE_PATH/$TARBALL
  "
fi

# $(tar -tzf "$REMOTE_PATH/$TARBALL") &&

  rm "$TARBALL"

echo "✅ Upload of modified/untracked files completed successfully."
