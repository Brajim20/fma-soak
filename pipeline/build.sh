#!/bin/zsh
# Build fleet + fleetctl at a revision into bin/fleet-<rev> and bin/fleetctl-<rev> for the L1 suite (task 2.8).
#   build.sh <rev>        e.g. build.sh 937e874   (the RC's revision: GET /version on the RC instance)
# Uses a temporary git worktree of the local fleet checkout, so the checkout itself is never switched.
# The suite only uses the API, so the frontend bundle isn't built.
set -e
HERE=${0:A:h}
REV=${1:?usage: build.sh <rev>}
REPO=${FMA_CATALOG_REPO:-$(python3 -c "import sys;sys.path.insert(0,'$HERE/..');import settings,json,os;print(settings.catalog_repo(json.load(open('$HERE/../config.local.json'))))")}
WT=$(mktemp -d)/fleet-$REV
git -C $REPO fetch -q origin
git -C $REPO worktree add -q --detach $WT $REV
trap "git -C $REPO worktree remove --force $WT" EXIT
cd $WT
# the `full` build needs server/bindata/generated.go (Makefile generate-go); without the webpack bundle it just
# embeds the committed templates and assets, which is all the API-only suite needs
mkdir -p assets
go run github.com/kevinburke/go-bindata/go-bindata -pkg=bindata -tags full -o=server/bindata/generated.go \
  frontend/templates/ assets/... server/mail/templates
# same version stamps as the Makefile, so /version (and every suite result) names the build
V=github.com/fleetdm/fleet/v4/server/version
LDF="-s -w -X $V.appName=fleet -X $V.version=$(git describe --tags --always --match 'fleet-v*') -X $V.revision=$(git rev-parse HEAD) -X $V.branch=detached -X $V.buildDate=$(date -u +%FT%TZ)"
CGO_ENABLED=1 go build -tags full,fts5,netgo -ldflags "$LDF" -o $HERE/bin/fleet-$REV ./cmd/fleet
CGO_ENABLED=0 go build -ldflags "${LDF/appName=fleet/appName=fleetctl}" -o $HERE/bin/fleetctl-$REV ./cmd/fleetctl
echo "built bin/fleet-$REV and bin/fleetctl-$REV"
