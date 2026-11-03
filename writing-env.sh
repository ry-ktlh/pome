# Source this file in the shell used for writing:
#   . "$HOME/.local/share/pome/writing-env.sh"
# For a custom install, export POME_PREFIX before sourcing it.
_pome_bin="${POME_PREFIX:-$HOME/.local}/bin"
if [ ! -x "$_pome_bin/pome" ]; then
    printf '%s\n' "pome is not installed in $_pome_bin; run install.sh first." >&2
    unset _pome_bin
    return 1
fi
case "${PATH:-}" in
    "$_pome_bin"|"$_pome_bin":*) ;;
    *) PATH="$_pome_bin${PATH:+:$PATH}" ;;
esac
EDITOR=pome
VISUAL=pome
export PATH EDITOR VISUAL
unset _pome_bin
