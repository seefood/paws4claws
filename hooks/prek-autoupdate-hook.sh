#!/usr/bin/env bash
# Pre-commit hook: runs 'prek autoupdate --freeze' when .pre-commit-config.yaml
# is staged, then re-stages the file only if revisions actually changed.
# The --cooldown-days flag makes this a no-op if hooks were updated recently,
# keeping the hook fast on routine commits.

set -e

staged_files=$(git diff --cached --name-only)

for file in $staged_files; do
	if [[ "$file" == ".pre-commit-config.yaml" ]]; then
		echo "Found staged .pre-commit-config.yaml — running prek autoupdate --freeze..."

		if prek autoupdate --freeze --cooldown-days 10; then
			# Only re-stage if prek actually changed the working tree copy
			if ! git diff --quiet .pre-commit-config.yaml; then
				if git add .pre-commit-config.yaml; then
					echo "Re-staged .pre-commit-config.yaml with updated frozen revisions"
				else
					echo "Error: failed to re-stage .pre-commit-config.yaml"
					exit 1
				fi
			fi
		else
			echo "Error: prek autoupdate failed"
			exit 1
		fi

		break
	fi
done

exit 0
