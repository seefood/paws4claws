#!/usr/bin/env bash
# Pre-commit hook to run 'uv lock' when pyproject.toml files are modified
# and add the updated lock files to the staged files

set -e

# Get list of staged files
staged_files=$(git diff --cached --name-only)

# Track if we need to re-add files
files_to_add=()

# Check each staged file
for file in $staged_files; do
	# If a pyproject.toml file is being committed
	if [[ "$file" == *"pyproject.toml" ]]; then
		# Get the directory containing the pyproject.toml
		dir=$(dirname "$file")

		echo "Found modified pyproject.toml in $dir, running uv lock..."

		# Change to the directory and run uv lock
		if (cd "$dir" && uv lock); then
			# Check if a lock file was created/modified
			lock_file="$dir/uv.lock"
			if [[ -f "$lock_file" ]]; then
				echo "Adding updated lock file: $lock_file"
				files_to_add+=("$lock_file")
			fi
		else
			echo "Error: uv lock failed in $dir"
			exit 1
		fi
	fi
done

# Add any updated lock files to the staged files
if [[ ${#files_to_add[@]} -gt 0 ]]; then
	git add "${files_to_add[@]}"
	echo "Added ${#files_to_add[@]} lock file(s) to the commit"
fi

exit 0
