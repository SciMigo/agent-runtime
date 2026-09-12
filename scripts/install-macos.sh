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

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="https://github.com/SciMigo/agent-runtime"
INSTALL_DIR="$HOME/.local/bin"
RUNTIME_DIR="$HOME/.agent-runtime"
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

# Check for Python
check_python() {
    echo -e "${BLUE}Checking Python installation...${NC}"

    # Try python3 first, then python
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        echo -e "${RED}Error: Python not found.${NC}"
        echo ""
        echo "Please install Python $MIN_PYTHON_VERSION or later:"
        echo "  brew install python@3.11"
        echo "  # or"
        echo "  https://www.python.org/downloads/"
        exit 1
    fi

    # Check version
    PYTHON_VERSION=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    REQUIRED_MAJOR=$(echo $MIN_PYTHON_VERSION | cut -d. -f1)
    REQUIRED_MINOR=$(echo $MIN_PYTHON_VERSION | cut -d. -f2)
    ACTUAL_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    ACTUAL_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

    if [[ $ACTUAL_MAJOR -lt $REQUIRED_MAJOR ]] || [[ $ACTUAL_MAJOR -eq $REQUIRED_MAJOR && $ACTUAL_MINOR -lt $REQUIRED_MINOR ]]; then
        echo -e "${RED}Error: Python $MIN_PYTHON_VERSION or later required (found $PYTHON_VERSION)${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Found Python $PYTHON_VERSION${NC}"
}

# Check for uv (recommended) or pip
check_package_manager() {
    echo -e "${BLUE}Checking package manager...${NC}"

    if command -v uv &> /dev/null; then
        PKG_MANAGER="uv"
        echo -e "${GREEN}✓ Found uv (recommended)${NC}"
    elif command -v pip3 &> /dev/null; then
        PKG_MANAGER="pip3"
        echo -e "${YELLOW}! Using pip3 (uv recommended for faster installs)${NC}"
        echo "  Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    elif command -v pip &> /dev/null; then
        PKG_MANAGER="pip"
        echo -e "${YELLOW}! Using pip (uv recommended for faster installs)${NC}"
    else
        echo -e "${RED}Error: No package manager found. Please install pip or uv.${NC}"
        exit 1
    fi
}

# Create installation directory
setup_dirs() {
    echo -e "${BLUE}Setting up directories...${NC}"

    mkdir -p "$INSTALL_DIR"
    mkdir -p "$RUNTIME_DIR"

    echo -e "${GREEN}✓ Created $RUNTIME_DIR${NC}"
}

# Install agent-runtime
install_runtime() {
    echo -e "${BLUE}Installing Agent Runtime...${NC}"

    if [[ "$PKG_MANAGER" == "uv" ]]; then
        uv pip install --system agent-runtime
    else
        $PKG_MANAGER install agent-runtime
    fi

    echo -e "${GREEN}✓ Installed Agent Runtime${NC}"
}

# Install from git (for development or if package not published)
install_from_git() {
    echo -e "${BLUE}Installing Agent Runtime from source...${NC}"

    TEMP_DIR=$(mktemp -d)
    git clone --depth 1 "$REPO_URL.git" "$TEMP_DIR/agent-runtime"

    cd "$TEMP_DIR/agent-runtime"

    if [[ "$PKG_MANAGER" == "uv" ]]; then
        uv pip install --system -e .
    else
        $PKG_MANAGER install -e .
    fi

    cd - > /dev/null
    rm -rf "$TEMP_DIR"

    echo -e "${GREEN}✓ Installed Agent Runtime from source${NC}"
}

# Add to PATH if needed
setup_path() {
    echo -e "${BLUE}Checking PATH...${NC}"

    # Check if agent-runtime is in PATH
    if command -v agent-runtime &> /dev/null; then
        echo -e "${GREEN}✓ agent-runtime is in PATH${NC}"
        return
    fi

    # Determine shell config file
    SHELL_NAME=$(basename "$SHELL")
    case "$SHELL_NAME" in
        bash)
            SHELL_RC="$HOME/.bashrc"
            ;;
        zsh)
            SHELL_RC="$HOME/.zshrc"
            ;;
        *)
            SHELL_RC=""
            ;;
    esac

    if [[ -n "$SHELL_RC" ]]; then
        echo "" >> "$SHELL_RC"
        echo "# Agent Runtime" >> "$SHELL_RC"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
        echo -e "${YELLOW}! Added $HOME/.local/bin to PATH in $SHELL_RC${NC}"
        echo "  Run: source $SHELL_RC"
    fi
}

# Verify installation
verify_install() {
    echo -e "${BLUE}Verifying installation...${NC}"

    # Try to import the package
    $PYTHON_CMD -c "import agent_runtime; print(f'Version: {agent_runtime.__version__}')" 2>/dev/null || {
        echo -e "${RED}Warning: Could not verify installation${NC}"
        return
    }

    echo -e "${GREEN}✓ Installation verified${NC}"
}

# Print success message
print_success() {
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║     Agent Runtime installed successfully! ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
    echo ""
    echo "Quick start:"
    echo "  ${BLUE}agent-runtime serve${NC}              # Start the runtime"
    echo "  ${BLUE}agent-runtime serve --no-pairing${NC} # Start without pairing (dev mode)"
    echo ""
    echo "Runtime will be available at: http://localhost:9477"
    echo ""
    echo "For more information:"
    echo "  ${BLUE}agent-runtime --help${NC}"
    echo "  $REPO_URL"
}

# Main installation flow
main() {
    check_python
    check_package_manager
    setup_dirs

    # Try to install from PyPI first, fall back to git
    install_runtime 2>/dev/null || install_from_git

    setup_path
    verify_install
    print_success
}

# Run main
main
