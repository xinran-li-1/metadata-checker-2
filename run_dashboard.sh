#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_FILE="dashboard_app.py"

if [ ! -f "$APP_FILE" ]; then
  echo "Error: $APP_FILE not found in $SCRIPT_DIR"
  echo "Please update APP_FILE in run_dashboard.sh to match your actual file name."
  exit 1
fi

if [ -d ".venv" ]; then
  # For bash/zsh on macOS
  source ".venv/bin/activate"
fi

if ! python -m streamlit --version >/dev/null 2>&1; then
  echo "Streamlit not found, installing with pip..."
  python -m pip install --upgrade pip
  python -m pip install streamlit
fi

python -m streamlit run "$APP_FILE"
