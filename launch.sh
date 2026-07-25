#!/bin/bash

################################################################################
# One-Click Launcher - University Admissions Voice AI Assistant
################################################################################
# This script provides a simple menu to:
# 1. Setup the environment (if not already done)
# 2. Start services
# 3. Stop services
# 4. View logs
#
# Usage:
#   bash launch.sh
################################################################################

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/venv"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

clear_screen() {
    clear
}

show_banner() {
    cat << 'EOF'
╔════════════════════════════════════════════════════════════════════╗
║                                                                    ║
║  🎓 University Admissions Voice AI Assistant                      ║
║     One-Click Launcher                                            ║
║                                                                    ║
║  Status: Ready for Deployment                                    ║
║  Version: 2.1.0                                                   ║
║                                                                    ║
╚════════════════════════════════════════════════════════════════════╝
EOF
    echo ""
}

show_menu() {
    echo -e "${CYAN}What would you like to do?${NC}"
    echo ""
    echo -e "${GREEN}1)${NC} 🚀 Setup & Start (First time setup)"
    echo -e "${GREEN}2)${NC} ▶️  Start Services (FastAPI + Streamlit)"
    echo -e "${GREEN}3)${NC} ⏹️  Stop Services"
    echo -e "${GREEN}4)${NC} 📋 View Logs"
    echo -e "${GREEN}5)${NC} 🐳 Docker Setup & Start (Alternative)"
    echo -e "${GREEN}6)${NC} 🔧 Environment Verification"
    echo -e "${GREEN}7)${NC} 📖 View Documentation"
    echo -e "${GREEN}8)${NC} 🌐 Open in Browser"
    echo -e "${GREEN}9)${NC} ❌ Exit"
    echo ""
    read -p "Enter your choice (1-9): " choice
}

check_setup() {
    if [ ! -d "$VENV_PATH" ] || [ ! -f "$PROJECT_ROOT/.env" ]; then
        return 1
    fi
    return 0
}

setup_and_start() {
    clear_screen
    show_banner
    
    echo -e "${BLUE}Running full setup...${NC}"
    echo ""
    
    if ! bash setup.sh; then
        echo -e "${RED}✗ Setup failed. Please check the errors above.${NC}"
        read -p "Press Enter to continue..."
        return 1
    fi
    
    echo ""
    echo -e "${BLUE}Setup complete. Starting services...${NC}"
    sleep 2
    
    bash start.sh
    
    echo ""
    echo -e "${GREEN}✓ Services started!${NC}"
    echo ""
    echo -e "${CYAN}Access your applications:${NC}"
    echo -e "  🌐 Web UI:      ${YELLOW}http://localhost:8501${NC}"
    echo -e "  📡 API Docs:    ${YELLOW}http://localhost:8000/docs${NC}"
    echo ""
    echo -e "${BLUE}Keep this terminal open or use 'bash launch.sh' to manage services.${NC}"
    read -p "Press Enter to continue..."
}

start_services() {
    clear_screen
    show_banner
    
    if ! check_setup; then
        echo -e "${RED}✗ Setup not complete. Please run 'Setup & Start' first.${NC}"
        read -p "Press Enter to continue..."
        return 1
    fi
    
    bash start.sh
    
    echo ""
    echo -e "${GREEN}✓ Services started!${NC}"
    echo ""
    echo -e "${CYAN}Access your applications:${NC}"
    echo -e "  🌐 Web UI:      ${YELLOW}http://localhost:8501${NC}"
    echo -e "  📡 API Docs:    ${YELLOW}http://localhost:8000/docs${NC}"
    echo ""
    read -p "Press Enter to continue..."
}

stop_services() {
    clear_screen
    show_banner
    
    echo -e "${BLUE}Stopping services...${NC}"
    bash stop.sh
    
    echo ""
    echo -e "${GREEN}✓ Services stopped!${NC}"
    read -p "Press Enter to continue..."
}

view_logs() {
    clear_screen
    show_banner
    
    echo -e "${CYAN}Select which logs to view:${NC}"
    echo ""
    echo -e "${GREEN}1)${NC} FastAPI logs"
    echo -e "${GREEN}2)${NC} Streamlit logs"
    echo -e "${GREEN}3)${NC} All logs"
    echo -e "${GREEN}4)${NC} Back to menu"
    echo ""
    read -p "Enter your choice (1-4): " log_choice
    
    case $log_choice in
        1)
            clear_screen
            show_banner
            echo -e "${CYAN}FastAPI Logs (Ctrl+C to exit):${NC}"
            echo ""
            tail -f "$PROJECT_ROOT/logs/fastapi.log" 2>/dev/null || echo "No logs yet. Start services first."
            ;;
        2)
            clear_screen
            show_banner
            echo -e "${CYAN}Streamlit Logs (Ctrl+C to exit):${NC}"
            echo ""
            tail -f "$PROJECT_ROOT/logs/streamlit.log" 2>/dev/null || echo "No logs yet. Start services first."
            ;;
        3)
            clear_screen
            show_banner
            echo -e "${CYAN}All Logs (Ctrl+C to exit):${NC}"
            echo ""
            tail -f "$PROJECT_ROOT/logs/*.log" 2>/dev/null || echo "No logs yet. Start services first."
            ;;
        4)
            return
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            ;;
    esac
}

docker_setup() {
    clear_screen
    show_banner
    
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}✗ Docker is not installed${NC}"
        read -p "Press Enter to continue..."
        return
    fi
    
    echo -e "${BLUE}Starting Docker Compose setup...${NC}"
    echo ""
    
    docker-compose up -d
    
    echo ""
    echo -e "${GREEN}✓ Docker containers started!${NC}"
    echo ""
    echo -e "${CYAN}Access your applications:${NC}"
    echo -e "  🌐 Web UI:      ${YELLOW}http://localhost:8501${NC}"
    echo -e "  📡 API Docs:    ${YELLOW}http://localhost:8000/docs${NC}"
    echo -e "  🦙 Ollama:      ${YELLOW}http://localhost:11434${NC}"
    echo ""
    echo -e "${BLUE}Monitor with: docker-compose logs -f${NC}"
    read -p "Press Enter to continue..."
}

verify_environment() {
    clear_screen
    show_banner
    
    echo -e "${BLUE}Verifying environment...${NC}"
    echo ""
    
    python3 test_environment.py
    
    echo ""
    read -p "Press Enter to continue..."
}

view_documentation() {
    clear_screen
    show_banner
    
    echo -e "${CYAN}Documentation available:${NC}"
    echo ""
    echo -e "${GREEN}1)${NC} SETUP_GUIDE.md - Detailed setup instructions"
    echo -e "${GREEN}2)${NC} QUICKSTART.md - Quick reference"
    echo -e "${GREEN}3)${NC} IMPLEMENTATION_COMPLETE.md - What was implemented"
    echo -e "${GREEN}4)${NC} DELIVERY_COMPLETE.md - Delivery summary"
    echo -e "${GREEN}5)${NC} README.md - Project overview"
    echo -e "${GREEN}6)${NC} Back to menu"
    echo ""
    read -p "Enter your choice (1-6): " doc_choice
    
    case $doc_choice in
        1) less SETUP_GUIDE.md 2>/dev/null || cat SETUP_GUIDE.md ;;
        2) less QUICKSTART.md 2>/dev/null || cat QUICKSTART.md ;;
        3) less IMPLEMENTATION_COMPLETE.md 2>/dev/null || cat IMPLEMENTATION_COMPLETE.md ;;
        4) less DELIVERY_COMPLETE.md 2>/dev/null || cat DELIVERY_COMPLETE.md ;;
        5) less README.md 2>/dev/null || cat README.md 2>/dev/null || echo "README.md not found" ;;
        6) return ;;
        *) echo -e "${RED}Invalid choice${NC}" ;;
    esac
}

open_browser() {
    clear_screen
    show_banner
    
    echo -e "${CYAN}Which application to open?${NC}"
    echo ""
    echo -e "${GREEN}1)${NC} 🌐 Web UI (Streamlit) - http://localhost:8501"
    echo -e "${GREEN}2)${NC} 📡 API Docs (FastAPI) - http://localhost:8000/docs"
    echo -e "${GREEN}3)${NC} Back to menu"
    echo ""
    read -p "Enter your choice (1-3): " browser_choice
    
    case $browser_choice in
        1)
            echo -e "${BLUE}Opening Web UI in browser...${NC}"
            if command -v open &> /dev/null; then
                open "http://localhost:8501"
            else
                echo -e "${YELLOW}Please open http://localhost:8501 in your browser${NC}"
            fi
            sleep 2
            ;;
        2)
            echo -e "${BLUE}Opening API Docs in browser...${NC}"
            if command -v open &> /dev/null; then
                open "http://localhost:8000/docs"
            else
                echo -e "${YELLOW}Please open http://localhost:8000/docs in your browser${NC}"
            fi
            sleep 2
            ;;
        3)
            return
            ;;
    esac
}

main_loop() {
    while true; do
        clear_screen
        show_banner
        show_menu
        
        case $choice in
            1)
                setup_and_start
                ;;
            2)
                start_services
                ;;
            3)
                stop_services
                ;;
            4)
                view_logs
                ;;
            5)
                docker_setup
                ;;
            6)
                verify_environment
                ;;
            7)
                view_documentation
                ;;
            8)
                open_browser
                ;;
            9)
                clear_screen
                echo -e "${GREEN}👋 Goodbye!${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}Invalid choice. Please try again.${NC}"
                sleep 2
                ;;
        esac
    done
}

# Run main loop
main_loop
