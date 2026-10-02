#!/bin/bash
cd ~/autoteams-deploy
cat > /tmp/login.json <<'EOF'
{"email":"demo@autoteams.example","password":"demo123456"}
EOF

DC="sudo docker compose -f docker-compose.prod.yml -f docker-compose.prod.override.yml --env-file .env.prod exec -T backend"

echo "=== RAW LOGIN ==="
$DC bash -c 'curl -s -X POST http://localhost:8000/api/auth/login -H "Content-Type: application/json" -d @/tmp/login.json' | head -c 600
echo ""
