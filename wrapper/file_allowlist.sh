# shellcheck shell=sh
# Shared file-detection allowlist for the PAWS wrapper (v0.3).
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

	# S3 high-level cp/mv/sync: positional local file paths only.
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
	case "$_arg" in
		--*) return 1 ;;
		s3://*) return 1 ;;
		-) return 1 ;;
	esac
	case "$_arg" in
		*..*) return 1 ;;
	esac
	if [ ! -f "$_arg" ]; then
		return 1
	fi
	_encode_path "$_arg"
}

# Build JSON array of {argIndex, content} for "$@". Prints JSON array.
collect_inline_files() {
	FILES='[]'
	_idx=0
	_prev=""
	_service=${1:-}
	_subcmd=${2:-}
	for _arg in "$@"; do
		if _b64=$(_should_inline_arg "$_prev" "$_arg" "$_idx" "$_service" "$_subcmd"); then
			FILES=$(printf '%s' "$FILES" | jq --argjson i "$_idx" --arg c "$_b64" \
				'. + [{argIndex: $i, content: $c}]')
		fi
		_prev=$_arg
		_idx=$((_idx + 1))
	done
	printf '%s' "$FILES"
}
