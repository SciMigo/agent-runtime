#!/bin/bash
#
# Agent Runtime installer for macOS
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/SciMigo/agent-runtime/main/scripts/install-macos.sh | bash
#
# Or download and run:
#   ./install-macos.sh
#
# Installs the latest code from GitHub into its own environment and puts the
# `agent-runtime` command in ~/.local/bin. Re-running the script upgrades it.
#
# Set AGENT_RUNTIME_SOURCE to install from somewhere else, e.g. a local
# checkout or another branch's archive URL.
#
# Note: "agent-runtime" on PyPI is an unrelated placeholder package, so this
# script never installs from PyPI.
#

set -e

# Colors for output ($'...' stores the real ESC byte, so they work with plain echo too).
# Disabled when stdout is not a terminal or NO_COLOR is set.
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    RED=$'\033[0;31m'
    GREEN=$'\033[0;32m'
    YELLOW=$'\033[1;33m'
    BLUE=$'\033[0;34m'
    NC=$'\033[0m' # No Color
else
    RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

# Configuration
REPO_URL="https://github.com/SciMigo/agent-runtime"
SOURCE="${AGENT_RUNTIME_SOURCE:-$REPO_URL/archive/refs/heads/main.tar.gz}"
INSTALL_DIR="$HOME/.local/bin"
VENV_DIR="$HOME/.agent-runtime/venv"
MIN_PYTHON_VERSION="3.11"

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════╗"
echo "║        Agent Runtime Installer            ║"
echo "║       Lightweight AI Agent Runtime        ║"
echo "╚═══════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running on macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}Error: This installer is for macOS only.${NC}"
    echo "For other platforms, see: $REPO_URL#installation"
    exit 1
fi

# Prefer uv; otherwise fall back to a venv created with a local Python
check_installer() {
    echo -e "${BLUE}Checking for uv...${NC}"

    if command -v uv &> /dev/null; then
        INSTALLER="uv"
        echo -e "${GREEN}✓ Found uv${NC}"
    else
        INSTALLER="venv"
        echo -e "${YELLOW}! uv not found, using Python venv + pip instead${NC}"
        echo "  (uv is faster: curl -LsSf https://astral.sh/uv/install.sh | sh)"
        check_python
    fi
}

# Find a Python that meets MIN_PYTHON_VERSION (only needed without uv)
check_python() {
    echo -e "${BLUE}Checking Python installation...${NC}"

    REQUIRED_MAJOR=$(echo $MIN_PYTHON_VERSION | cut -d. -f1)
    REQUIRED_MINOR=$(echo $MIN_PYTHON_VERSION | cut -d. -f2)

    # python3 may be an older system Python, so also try versioned names
    for cmd in python3 python3.14 python3.13 python3.12 python3.11; do
        command -v "$cmd" &> /dev/null || continue

        PYTHON_VERSION=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || continue
        ACTUAL_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        ACTUAL_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

        if [[ $ACTUAL_MAJOR -gt $REQUIRED_MAJOR ]] || [[ $ACTUAL_MAJOR -eq $REQUIRED_MAJOR && $ACTUAL_MINOR -ge $REQUIRED_MINOR ]]; then
            PYTHON_CMD="$cmd"
            echo -e "${GREEN}✓ Found Python $PYTHON_VERSION ($PYTHON_CMD)${NC}"
            return
        fi
    done

    echo -e "${RED}Error: Python $MIN_PYTHON_VERSION or later not found.${NC}"
    echo ""
    echo "Install one of:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh   # recommended, manages Python for you"
    echo "  brew install python@3.12"
    echo "  https://www.python.org/downloads/"
    exit 1
}

# Install agent-runtime
install_runtime() {
    echo -e "${BLUE}Installing Agent Runtime from $SOURCE...${NC}"

    if [[ "$INSTALLER" == "uv" ]]; then
        # --reinstall re-fetches the source, so re-running upgrades to the latest code
        if ! uv tool install --force --reinstall --python ">=$MIN_PYTHON_VERSION" --from "$SOURCE" agent-runtime; then
            echo -e "${RED}Error: Installation failed (see the uv output above).${NC}"
            exit 1
        fi
        INSTALL_DIR=$(uv tool dir --bin)
    else
        mkdir -p "$INSTALL_DIR" "$(dirname "$VENV_DIR")"
        if ! { "$PYTHON_CMD" -m venv --clear "$VENV_DIR" &&
               "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check "$SOURCE"; }; then
            echo -e "${RED}Error: Installation failed (see the pip output above).${NC}"
            exit 1
        fi
        ln -sf "$VENV_DIR/bin/agent-runtime" "$INSTALL_DIR/agent-runtime"
    fi

    echo -e "${GREEN}✓ Installed Agent Runtime${NC}"
}

# Add INSTALL_DIR to PATH if needed
setup_path() {
    echo -e "${BLUE}Checking PATH...${NC}"

    case ":$PATH:" in
        *":$INSTALL_DIR:"*)
            echo -e "${GREEN}✓ $INSTALL_DIR is in PATH${NC}"
            return
            ;;
    esac

    # Terminal.app opens login shells, so bash reads .bash_profile rather than .bashrc
    case "$(basename "$SHELL")" in
        zsh)  SHELL_RC="$HOME/.zshrc" ;;
        bash) SHELL_RC="$HOME/.bash_profile" ;;
        *)    SHELL_RC="" ;;
    esac

    if [[ -z "$SHELL_RC" ]]; then
        echo -e "${YELLOW}! Add $INSTALL_DIR to your PATH to use agent-runtime${NC}"
        RELOAD_HINT="export PATH=\"$INSTALL_DIR:\$PATH\""
        return
    fi

    # Write paths under $HOME as "$HOME/..." to match what most rc files use
    if [[ "$INSTALL_DIR" == "$HOME"/* ]]; then
        RC_DIR="\$HOME${INSTALL_DIR#"$HOME"}"
    else
        RC_DIR="$INSTALL_DIR"
    fi
    PATH_LINE="export PATH=\"$RC_DIR:\$PATH\""

    if grep -qsF "$PATH_LINE" "$SHELL_RC"; then
        echo -e "${GREEN}✓ $SHELL_RC already adds $INSTALL_DIR to PATH${NC}"
    else
        {
            echo ""
            echo "# Agent Runtime"
            echo "$PATH_LINE"
        } >> "$SHELL_RC"
        echo -e "${YELLOW}! Added $INSTALL_DIR to PATH in $SHELL_RC${NC}"
    fi
    RELOAD_HINT="source $SHELL_RC"
}

# Verify installation
verify_install() {
    echo -e "${BLUE}Verifying installation...${NC}"

    if ! VERSION_OUTPUT=$("$INSTALL_DIR/agent-runtime" --version 2>&1); then
        echo -e "${RED}Error: agent-runtime was installed but failed to run:${NC}"
        echo "$VERSION_OUTPUT"
        exit 1
    fi

    echo -e "${GREEN}✓ $VERSION_OUTPUT${NC}"
}

# Print success message
print_success() {
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║     Agent Runtime installed successfully! ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
    echo ""
    if [[ -n "$RELOAD_HINT" ]]; then
        echo -e "${YELLOW}First, open a new terminal window or run:${NC}"
        echo "  ${BLUE}$RELOAD_HINT${NC}"
        echo ""
    fi
    echo "Quick start:"
    echo "  ${BLUE}agent-runtime serve${NC}              # Start the runtime"
    echo "  ${BLUE}agent-runtime serve --no-pairing${NC} # Start without pairing (dev mode)"
    echo "  ${BLUE}agent-runtime tls setup${NC}          # Once: HTTPS on 127.0.0.1:9478, needed by Safari"
    echo ""
    echo "Runtime will be available at: http://localhost:9477 (and https://127.0.0.1:9478 after tls setup)"
    echo ""
    echo "For more information:"
    echo "  ${BLUE}agent-runtime --help${NC}"
    echo "  $REPO_URL"
}

# Main installation flow
main() {
    check_installer
    install_runtime
    setup_path
    verify_install
    print_success
}

# Run main
main
