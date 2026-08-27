#!/usr/bin/env bash

# 1. Check if Ollama service/daemon is running
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚡ [SHADOW CORE]: Starting Ollama background server..."
    ollama serve > /tmp/ollama_daemon.log 2>&1 &
    
    # Wait until Ollama API is responsive
    until curl -s http://localhost:11434/api/tags > /dev/null; do
        sleep 1
    done
    echo "✓ [SHADOW CORE]: Ollama server initialized."
else
    echo "✓ [SHADOW CORE]: Ollama server is already running."
fi

# 2. Ensure default models are present in local storage
echo "⚡ [SHADOW CORE]: Verifying base local models..."
ollama pull qwen2.5-coder:latest > /dev/null 2>&1 &

# 3. Run Shadow Core Python HUD
python3 shadow_core.py
