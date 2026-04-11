#!/bin/bash

usage() {
    echo "Usage: $0 <file1> [file2 ...] [-m <commit_message_file>] [--per-line]"
    echo ""
    echo "  -m, --message-file   Path to a file containing commit message(s)"
    echo "  --per-line            Each file gets a separate commit from each line in the message file"
    exit 1
}

if [ $# -lt 1 ]; then
    usage
fi

files_to_add=()
message_file=""
per_line=false
commit_msg=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--message-file)
            shift
            message_file="$1"
            ;;
        --per-line)
            per_line=true
            ;;
        -*)
            echo "Unknown option: $1"
            usage
            ;;
        *)
            files_to_add+=("$1")
            ;;
    esac
    shift
done

if [ ${#files_to_add[@]} -eq 0 ]; then
    echo "Error: No files specified."
    usage
fi

for file in "${files_to_add[@]}"; do
    if [ -e "$file" ]; then
        git add "$file"
        echo "Added $file"
    else
        echo "Warning: File '$file' does not exist"
    fi
done
echo "Added $files_to_add"

if [ -n "$message_file" ]; then
    if [ ! -f "$message_file" ]; then
        echo "Error: Commit message file '$message_file' does not exist."
        exit 1
    fi

    if $per_line; then
        echo "Per-line commit mode enabled"
        mapfile -t messages < "$message_file"

        if [ "${#messages[@]}" -lt "${#files_to_add[@]}" ]; then
            echo "Error: Not enough lines in $message_file for the number of files."
            exit 1
        fi

        for i in "${!files_to_add[@]}"; do
            msg="${messages[$i]}"
            file="${files_to_add[$i]}"
            echo "Committing $file with message: $msg"
            git commit "$file" -m "$msg"
        done
        exit 0
    else
    	commit_msg=$(<"$message_file")
    fi
else
    echo "Enter multiline commit message (type 'END' on a new line to finish):"
    while IFS= read -e -r line && [ "$line" != "END" ]; do
        commit_msg+="$line"$'\n'
    done
fi

commit_msg="${commit_msg%$'\n'}"

echo "Multiline input received:"
echo "-------------------------"
echo "$commit_msg"
echo "-------------------------"

echo "Committing all files with one message"
# git commit --date='2026-03-17 23:12:00' -m "$commit_msg"
git commit -m "$commit_msg"
# git push
