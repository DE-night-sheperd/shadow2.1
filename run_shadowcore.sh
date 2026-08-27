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

# 3. Ensure autonomy dependencies are present (filesystem + screen watching)
python3 -c "import watchdog" 2>/dev/null || pip install --quiet watchdog
python3 -c "import imagehash" 2>/dev/null || pip install --quiet Pillow imagehash

# 4. Ensure sensor dependencies are present (camera + voice; location needs no extra package)
python3 -c "import cv2" 2>/dev/null || pip install --quiet opencv-python
python3 -c "import sounddevice, numpy" 2>/dev/null || pip install --quiet sounddevice numpy
python3 -c "import whisper" 2>/dev/null || pip install --quiet openai-whisper

# 5. Run Shadow Core Python HUD
python3 shadow_core.py
