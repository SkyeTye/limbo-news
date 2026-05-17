#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
  echo "No .env file found. Copy .env.example to .env and add your ANTHROPIC_API_KEY."
  exit 1
fi

if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

echo ""
echo "Starting Anti-Limbo News..."
echo "Open http://localhost:5050"
echo ""

python backend/server.py
