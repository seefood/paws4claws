# shellcheck shell=sh
# Resolves which PAWS_URL/PAWS_TOKEN pair the wrapper uses for multi-account
# routing (AWS_PROFILE -> PAWS_URL_<PROFILE>/PAWS_TOKEN_<PROFILE>).
# Sourced by wrapper/aws — do not execute directly.

# Uppercase and replace non-alnum with '_': "acct-a" -> "ACCT_A".
_paws_profile_suffix() {
	printf '%s' "$1" | tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9' '_'
}

# Space-separated list of configured profile suffixes (from PAWS_URL_* env vars).
_paws_configured_profiles() {
	env | sed -n 's/^PAWS_URL_\([A-Za-z0-9_]*\)=.*/\1/p' | sort | tr '\n' ' ' | sed 's/ $//'
}

# On success: sets PAWS_URL and PAWS_TOKEN, returns 0.
# On failure: prints a "paws: ..." message to stderr, returns 1.
resolve_paws_target() {
	if [ -n "$AWS_PROFILE" ]; then
		_suffix=$(_paws_profile_suffix "$AWS_PROFILE")
		# shellcheck disable=SC2294
		eval "PAWS_URL=\${PAWS_URL_${_suffix}}"
		# shellcheck disable=SC2294
		eval "PAWS_TOKEN=\${PAWS_TOKEN_${_suffix}}"
		if [ -z "$PAWS_URL" ] || [ -z "$PAWS_TOKEN" ]; then
			_configured=$(_paws_configured_profiles)
			echo "paws: no PAWS_URL_${_suffix}/PAWS_TOKEN_${_suffix} configured for AWS_PROFILE=$AWS_PROFILE (configured: ${_configured:-none})" >&2
			return 1
		fi
		return 0
	fi

	PAWS_URL="${PAWS_URL:-http://paws:7142}"
	return 0
}
