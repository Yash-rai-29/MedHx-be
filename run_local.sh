#!/usr/bin/env bash
set -e

# AI Health Companion Local Run Script

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}      AI Health Companion — Local Runner          ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo

echo "Select how you would like to run the backend services:"
echo "1) Start BOTH services using Docker Compose (Recommended)"
echo "2) Start Patient Service locally (Python - Port 8001)"
echo "3) Start Doctor Service locally (Python - Port 8002)"
echo "4) Run import & verification checks"
echo "5) Exit"
echo

read -rp "Enter choice [1-5]: " choice
echo

case $choice in
  1)
    echo -e "${GREEN}Starting both services with Docker Compose...${NC}"
    docker-compose up --build
    ;;
  2)
    echo -e "${GREEN}Starting Patient Service locally...${NC}"
    if [ -f ".env" ]; then
      export $(grep -v '^#' .env | xargs)
    fi
    .venv/bin/python -m patient_service.main
    ;;
  3)
    echo -e "${GREEN}Starting Doctor Service locally...${NC}"
    if [ -f ".env" ]; then
      export $(grep -v '^#' .env | xargs)
    fi
    .venv/bin/python -m doctor_service.main
    ;;
  4)
    echo -e "${YELLOW}Running verification suite...${NC}"
    .venv/bin/python verify_imports.py
    ;;
  5)
    echo "Exiting."
    exit 0
    ;;
  *)
    echo "Invalid choice. Exiting."
    exit 1
    ;;
esac
