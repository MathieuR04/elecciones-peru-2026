#!/bin/bash
cd ~/Documents/elecciones-peru-2026
while true; do
    echo "⏱ $(date '+%H:%M:%S') — Actualizando..."
    git stash -q
    git pull --rebase -q
    git stash pop -q 2>/dev/null || true
    python3 scrapping/scrapper.py
    git add data/resultados.csv data/config.json data/historico.csv data/candidatos.json
    git diff --staged --quiet || git commit -m "Update results $(date '+%H:%M')"
    git stash -q 2>/dev/null || true
    git pull --rebase -q
    git stash pop -q 2>/dev/null || true
    git push -q
    echo "✅ $(date '+%H:%M:%S') — Listo. Esperando 3 minutos..."
    sleep 180
done
