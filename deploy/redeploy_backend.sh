#!/bin/bash
cd ~/autoteams-deploy
DC="sudo docker compose -f docker-compose.prod.yml -f docker-compose.prod.override.yml --env-file .env.prod"
echo "=== pull backend ==="
$DC pull backend
echo "=== verify image has ai_cost ==="
sudo docker run --rm --entrypoint grep ghcr.io/guoyangzhen/autoteams-backend:latest -l 'ai_cost' /app/app/services/evolution/org_analytics.py && echo "ai_cost PRESENT"
echo "=== force recreate backend ==="
$DC up -d --force-recreate backend
sleep 8
echo "=== status ==="
$DC ps backend
