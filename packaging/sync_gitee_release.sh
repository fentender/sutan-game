#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <version> <asset-path>" >&2
  exit 2
fi

: "${GITEE_TOKEN:?GITEE_TOKEN is required}"

VERSION="$1"
ASSET_PATH="$2"
TAG="v${VERSION}"
ASSET_NAME="$(basename "$ASSET_PATH")"
TITLE="[v${VERSION}] Mod 冲突合并工具"
API="https://gitee.com/api/v5/repos/fentende125/sutan-game/releases"

if [ ! -f "$ASSET_PATH" ]; then
  echo "Release asset not found: $ASSET_PATH" >&2
  exit 1
fi

ASSET_SIZE="$(stat -c '%s' "$ASSET_PATH")"
CURL_OPTS=(
  --silent
  --show-error
  --fail-with-body
  --location
  --connect-timeout 30
  --max-time 600
  --retry 3
  --retry-delay 5
  --retry-all-errors
)

release_json="$(curl "${CURL_OPTS[@]}" "$API")"
release_id="$(
  printf '%s' "$release_json" |
    python3 -c 'import json,sys; tag=sys.argv[1]; data=json.load(sys.stdin); print(next((item["id"] for item in data if item.get("tag_name") == tag), ""))' "$TAG"
)"

if [ -z "$release_id" ]; then
  echo "Creating Gitee release $TAG"
  create_json="$(
    curl "${CURL_OPTS[@]}" -X POST "$API" \
      -F "access_token=${GITEE_TOKEN}" \
      -F "tag_name=${TAG}" \
      -F "name=${TITLE}" \
      -F "body=CI 自动发布 v${VERSION}" \
      -F "target_commitish=master"
  )"
  release_id="$(printf '%s' "$create_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))')"
fi

if [ -z "$release_id" ]; then
  echo "Gitee did not return a release ID for $TAG" >&2
  exit 1
fi

attachments_api="${API}/${release_id}/attach_files"
echo "Uploading $ASSET_NAME ($ASSET_SIZE bytes) to Gitee release $TAG"

upload_json=""
if ! upload_json="$(
  curl "${CURL_OPTS[@]}" -X POST "$attachments_api" \
    -F "access_token=${GITEE_TOKEN}" \
    -F "file=@${ASSET_PATH}"
)"; then
  echo "Gitee rejected the release asset upload" >&2
  if [ -n "$upload_json" ]; then
    printf '%s\n' "$upload_json" >&2
  fi
  exit 1
fi

uploaded_id="$(
  UPLOAD_JSON="$upload_json" EXPECTED_NAME="$ASSET_NAME" EXPECTED_SIZE="$ASSET_SIZE" \
    python3 -c '
import json
import os

item = json.loads(os.environ["UPLOAD_JSON"])
expected_name = os.environ["EXPECTED_NAME"]
expected_size = int(os.environ["EXPECTED_SIZE"])
if item.get("name") != expected_name:
    raise SystemExit("Gitee returned unexpected attachment name: {!r}".format(item.get("name")))
if int(item.get("size", -1)) != expected_size:
    raise SystemExit("Gitee returned unexpected attachment size: {!r}".format(item.get("size")))
if not item.get("id"):
    raise SystemExit("Gitee upload response has no attachment ID")
print(item["id"])
'
)"

# Keep the newly uploaded platform asset. For the other platform, keep the
# oldest attachment and remove accidental duplicates left by older workflows.
attachments_json="$(curl "${CURL_OPTS[@]}" "$attachments_api")"
delete_ids="$(
  ATTACHMENTS_JSON="$attachments_json" TARGET_NAME="$ASSET_NAME" KEEP_ID="$uploaded_id" VERSION="$VERSION" \
    python3 -c '
import collections
import json
import os

items = json.loads(os.environ["ATTACHMENTS_JSON"])
target_name = os.environ["TARGET_NAME"]
keep_id = int(os.environ["KEEP_ID"])
version = os.environ["VERSION"]
managed_names = {
    f"SuDanModMerger-Windows-V{version}.zip",
    f"SuDanModMerger-macOS-V{version}.zip",
}
groups = collections.defaultdict(list)
for item in items:
    if item.get("name") in managed_names:
        groups[item["name"]].append(item)
for name, group in groups.items():
    selected_id = keep_id if name == target_name else min(int(item["id"]) for item in group)
    for item in group:
        if int(item["id"]) != selected_id:
            print(item["id"])
'
)"

while IFS= read -r attachment_id; do
  if [ -z "$attachment_id" ]; then
    continue
  fi
  echo "Removing duplicate Gitee attachment $attachment_id"
  curl "${CURL_OPTS[@]}" -X DELETE "${attachments_api}/${attachment_id}" \
    --data-urlencode "access_token=${GITEE_TOKEN}" >/dev/null
done <<< "$delete_ids"

final_json="$(curl "${CURL_OPTS[@]}" "$attachments_api")"
ATTACHMENTS_JSON="$final_json" TARGET_NAME="$ASSET_NAME" TARGET_ID="$uploaded_id" EXPECTED_SIZE="$ASSET_SIZE" VERSION="$VERSION" \
  python3 -c '
import collections
import json
import os

items = json.loads(os.environ["ATTACHMENTS_JSON"])
target_name = os.environ["TARGET_NAME"]
target_id = int(os.environ["TARGET_ID"])
expected_size = int(os.environ["EXPECTED_SIZE"])
version = os.environ["VERSION"]
managed_names = {
    f"SuDanModMerger-Windows-V{version}.zip",
    f"SuDanModMerger-macOS-V{version}.zip",
}
managed = [item for item in items if item.get("name") in managed_names]
counts = collections.Counter(item["name"] for item in managed)
target = [item for item in managed if item["name"] == target_name]
if len(target) != 1:
    raise SystemExit(f"Expected one {target_name} attachment, found {len(target)}")
if int(target[0]["id"]) != target_id or int(target[0].get("size", -1)) != expected_size:
    raise SystemExit("The final Gitee attachment does not match the uploaded file")
duplicates = {name: count for name, count in counts.items() if count != 1}
if duplicates:
    raise SystemExit(f"Duplicate managed attachments remain: {duplicates}")
for item in sorted(managed, key=lambda value: value["name"]):
    print("Verified Gitee attachment: {} ({} bytes, id {})".format(item["name"], item["size"], item["id"]))
'
