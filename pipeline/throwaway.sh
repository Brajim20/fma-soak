#!/bin/zsh
# L1 throwaway Fleet server for the update-pipeline suite (#55961, task 2.1).
#
#   throwaway.sh up   [build]   create DB, start the local FMA origin + Fleet, set up an admin (ctl.yml)
#   throwaway.sh down           stop Fleet + origin, drop the DB, flush the Redis DB, delete state
#
# build = suffix of bin/fleet-<build> and bin/fleetctl-<build> (default 937e874, qa.fleetdm.com's build).
# Isolation: own MySQL DB, own Redis DB, own S3 prefix, own ports — never touches qa.fleetdm.com or the dev servers.
set -e
HERE=${0:A:h}
BUILD=${2:-937e874}
PORT=8120; ORIGIN_PORT=8197; RDB=${PIPE_REDIS_DB:-15}; DB=fleet_qa_pipe; PREFIX=qa-pipe/
# Another QA server took Redis DB 10 (2026-10-30); before up/down, make sure no other Fleet server uses ours.
redis_db_taken() { ps -ax -o pid=,command= | grep -E "[f]leet.* serve " | grep -v "server_address localhost:$PORT" | grep -qE -- "--redis_database $RDB( |$)"; }
ST=$HERE/state; mkdir -p $ST
FLEET=$HERE/bin/fleet-$BUILD; CTL=$HERE/bin/fleetctl-$BUILD
mysql_root() { docker exec fleet-mysql-1 mysql -uroot -ptoor -e "$1" 2>&1 | grep -v Warning || true; }

case $1 in
up)
  for p in $PORT $ORIGIN_PORT; do lsof -iTCP:$p -sTCP:LISTEN >/dev/null && { echo "port $p busy"; exit 1; }; done
  redis_db_taken && { echo "redis db $RDB is used by another Fleet server; set PIPE_REDIS_DB"; exit 1; }
  [ "$(docker exec fleet-redis-1 redis-cli -n $RDB dbsize)" = 0 ] || { echo "redis db $RDB not empty"; exit 1; }
  mysql_root "drop database if exists $DB; create database $DB character set utf8mb4; grant all on $DB.* to 'fleet'@'%';"
  [ -f $ST/privkey ] || openssl rand -hex 32 > $ST/privkey
  COMMON=(--mysql_address 127.0.0.1:3306 --mysql_database $DB --mysql_username fleet --mysql_password insecure)
  $FLEET prepare db --dev "${COMMON[@]}" > $ST/prepare.log 2>&1
  nohup python3 $HERE/origin.py serve $ST/origin $ORIGIN_PORT > $ST/origin.log 2>&1 &
  echo $! > $ST/origin.pid
  FLEET_S3_SOFTWARE_INSTALLERS_PREFIX=$PREFIX FLEET_S3_CARVES_PREFIX=$PREFIX FLEET_S3_PREFIX=$PREFIX \
  FLEET_SERVER_PRIVATE_KEY=$(cat $ST/privkey) \
  FLEET_DEV_MAINTAINED_APPS_BASE_URL=http://127.0.0.1:$ORIGIN_PORT \
  FLEET_DEV_MAINTAINED_APPS_FALLBACK_BASE_URL=http://127.0.0.1:$ORIGIN_PORT \
  FLEET_VULNERABILITIES_DISABLE_DATA_SYNC=true FLEET_VULNERABILITIES_DATABASES_PATH=$ST/vulndb \
  FLEET_OSQUERY_STATUS_LOG_FILE=$ST/osq-status.log FLEET_OSQUERY_RESULT_LOG_FILE=$ST/osq-result.log \
  nohup $FLEET serve --dev --dev_license --logging_debug --server_address localhost:$PORT --server_tls=false \
    "${COMMON[@]}" --redis_address localhost:6379 --redis_database $RDB > $ST/server.log 2>&1 &
  echo $! > $ST/server.pid
  for i in {1..60}; do curl -s -o /dev/null -w '%{http_code}' http://localhost:$PORT/healthz | grep -q 200 && break; sleep 2; done
  grep -q "address already in use" $ST/server.log && { echo "server failed: address in use"; exit 1; }
  openssl rand -hex 10 > $ST/adminpw
  $CTL config set --config $ST/ctl.yml --address http://localhost:$PORT >/dev/null
  $CTL setup --config $ST/ctl.yml --email qa-pipe@example.com --name "QA pipeline" --password "Qa-$(cat $ST/adminpw)!" --org-name qa-pipe >/dev/null
  echo "up: http://localhost:$PORT ($(curl -s localhost:$PORT/version | python3 -c 'import json,sys;v=json.load(sys.stdin);print(v["version"], v["revision"][:10])'))"
  ;;
down)
  for f in server origin; do [ -f $ST/$f.pid ] && kill $(cat $ST/$f.pid) 2>/dev/null || true; done
  sleep 2
  mysql_root "drop database if exists $DB;"
  if redis_db_taken; then echo "redis db $RDB is used by another Fleet server; not flushing it"
  else docker exec fleet-redis-1 redis-cli -n $RDB flushdb >/dev/null; fi
  rm -rf $ST
  echo down
  ;;
*) echo "usage: throwaway.sh up [build] | down"; exit 1 ;;
esac
