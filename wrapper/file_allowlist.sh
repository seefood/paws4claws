# shellcheck shell=sh
# Shared file-detection allowlist for the PAWS wrapper (v0.3 input, v0.4 output).
# Sourced by wrapper/aws — do not execute directly.

# Parameters whose value is a file:// or fileb:// URI (see docs/aws-file-input.md).
FILE_PARAM_FLAGS="
	--user-data
	--payload
	--value
	--secret-string
	--secret-binary
	--template-body
	--policy-document
	--image-manifest
"

# Return 0 if flag expects a file URI value.
_is_file_param_flag() {
	_flag=$1
	for _f in $FILE_PARAM_FLAGS; do
		if [ "$_flag" = "$_f" ]; then
			return 0
		fi
	done
	return 1
}

# Resolve file:// or fileb:// to a path; print path or return 1.
_resolve_file_uri() {
	_arg=$1
	case "$_arg" in
		fileb://*)
			_path=${_arg#fileb://}
			;;
		file://*)
			_path=${_arg#file://}
			;;
		*)
			return 1
			;;
	esac
	case "$_path" in
		*..*) return 1 ;;
		/dev/stdin) return 1 ;;
	esac
	if [ ! -f "$_path" ]; then
		return 1
	fi
	printf '%s' "$_path"
}

# Base64-encode file at path; print base64 or return 1.
_encode_path() {
	_path=$1
	base64 <"$_path" | tr -d '\n'
}

# Return 0 if this argv slot should be considered for file inlining.
_should_inline_arg() {
	_prev=$1
	_arg=$2
	_idx=$3
	_service=$4
	_subcmd=$5

	# Known file-parameter value: must be file:// or fileb:// URI.
	if _is_file_param_flag "$_prev"; then
		if _path=$(_resolve_file_uri "$_arg"); then
			_encode_path "$_path"
			return 0
		fi
		return 1
	fi

	# S3 cp/mv/sync upload paths (full argv starts at $6).
	shift 5
	if _should_inline_s3_positional "$_idx" "$_service" "$_subcmd" "$@"; then
		_encode_path "$_arg"
		return 0
	fi
	return 1
}

# Write outputFiles from daemon response to original argv paths.
write_output_files() {
	_response=$1
	shift
	_count=$#
	_out=$(printf '%s' "$_response" | jq -c '.outputFiles // empty')
	if [ -z "$_out" ] || [ "$_out" = "null" ]; then
		return 0
	fi
	_len=$(printf '%s' "$_out" | jq 'length')
	_n=0
	while [ "$_n" -lt "$_len" ]; do
		_entry=$(printf '%s' "$_out" | jq -c ".[$_n]")
		_idx=$(printf '%s' "$_entry" | jq -r '.argIndex')
		_b64=$(printf '%s' "$_entry" | jq -r '.content')
		_n=$((_n + 1))
		if [ "$_idx" -lt 0 ] || [ "$_idx" -ge "$_count" ]; then
			echo "paws: outputFiles argIndex $_idx out of range" >&2
			return 1
		fi
		_dest=""
		_pos=1
		for _a in "$@"; do
			if [ "$_pos" -eq $((_idx + 1)) ]; then
				_dest=$_a
				break
			fi
			_pos=$((_pos + 1))
		done
		_dir=$(dirname "$_dest")
		if [ "$_dir" != "." ] && [ "$_dir" != "$_dest" ]; then
			mkdir -p "$_dir" || return 1
		fi
		_tmp=$(mktemp "${TMPDIR:-/tmp}/paws-out.XXXXXX") || return 1
		if ! printf '%s' "$_b64" | base64 -d >"$_tmp" 2>/dev/null; then
			rm -f "$_tmp"
			echo "paws: invalid base64 in outputFiles[$_idx]" >&2
			return 1
		fi
		mv -f "$_tmp" "$_dest" || {
			rm -f "$_tmp"
			return 1
		}
	done
}

# Return 0 if arg is a local positional (not s3://, not -, not a flag).
_is_local_positional() {
	_arg=$1
	case "$_arg" in
		--* | s3://* | -) return 1 ;;
	esac
	return 0
}

# Classify S3 cp/mv local slot: print "input", "output", or nothing.
_classify_s3_local_slot() {
	_idx=$1
	_service=$2
	_subcmd=$3
	shift 3
	if [ "$_service" != "s3" ]; then
		return 1
	fi
	case "$_subcmd" in
		cp | mv) ;;
		*) return 1 ;;
	esac
	# shellcheck disable=SC2039,SC2294
	eval "_arg=\${$((_idx + 1))}"
	if ! _is_local_positional "$_arg"; then
		return 1
	fi
	_first_s3=-1
	_last_s3=-1
	_i=0
	for _a in "$@"; do
		case "$_a" in
			s3://*)
				if [ "$_first_s3" -lt 0 ]; then
					_first_s3=$_i
				fi
				_last_s3=$_i
				;;
		esac
		_i=$((_i + 1))
	done
	if [ "$_first_s3" -lt 0 ]; then
		return 1
	fi
	if [ "$_idx" -lt "$_first_s3" ]; then
		printf '%s' "input"
		return 0
	fi
	if [ "$_idx" -gt "$_last_s3" ]; then
		printf '%s' "output"
		return 0
	fi
	return 1
}

# True if argv is upload-only local (needs existing file for v0.3 inline).
_should_inline_s3_positional() {
	_idx=$1
	_service=$2
	_subcmd=$3
	shift 3
	if [ "$_idx" -lt 2 ]; then
		return 1
	fi
	if [ "$_service" != "s3" ]; then
		return 1
	fi
	case "$_subcmd" in
		cp | mv | sync) ;;
		*) return 1 ;;
	esac
	# shellcheck disable=SC2039,SC2294
	eval "_arg=\${$((_idx + 1))}"
	if ! _is_local_positional "$_arg"; then
		return 1
	fi
	_kind=$(_classify_s3_local_slot "$_idx" "$_service" "$_subcmd" "$@")
	if [ "$_kind" != "input" ]; then
		return 1
	fi
	case "$_arg" in
		*..*) return 1 ;;
	esac
	if [ ! -f "$_arg" ]; then
		return 1
	fi
	return 0
}

# Build JSON array of {argIndex, content} for "$@". Prints JSON array.
collect_inline_files() {
	FILES='[]'
	_idx=0
	_prev=""
	_service=${1:-}
	_subcmd=${2:-}
	for _arg in "$@"; do
		if _b64=$(_should_inline_arg "$_prev" "$_arg" "$_idx" "$_service" "$_subcmd" "$@"); then
			FILES=$(printf '%s' "$FILES" | jq --argjson i "$_idx" --arg c "$_b64" \
				'. + [{argIndex: $i, content: $c}]')
		fi
		_prev=$_arg
		_idx=$((_idx + 1))
	done
	printf '%s' "$FILES"
}
