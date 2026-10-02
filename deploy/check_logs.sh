#!/bin/bash
cd ~/autoteams-deploy || exit 1
echo "=== containers ==="
sudo docker compose -f docker-compose.prod.yml ps --format "{{.Name}} {{.Status}}"
echo "=== backend recent logs ==="
sudo docker logs --tail 60 autoteams-deploy-backend-1 2>&1 | tail -60
