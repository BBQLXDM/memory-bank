#!/usr/bin/env bash
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

MAVEN_REPO_LOCAL="${MAVEN_REPO_LOCAL:-/home/zzx/.m2/repository}"
MAVEN_SETTINGS_FILE="${MAVEN_SETTINGS_FILE:-/home/zzx/.m2/settings.xml}"

export GIT_CONFIG_GLOBAL="${GIT_CONFIG_GLOBAL:-/home/zzx/.gitconfig}"
export GIT_CONFIG_NOSYSTEM="${GIT_CONFIG_NOSYSTEM:-1}"

if [[ ! -w "$MAVEN_REPO_LOCAL" ]]; then
  printf 'Maven 本地仓库不可写：%s\n' "$MAVEN_REPO_LOCAL" >&2
  printf '请先修复目录权限，或运行：MAVEN_REPO_LOCAL=/tmp/memind-m2-repository %s\n' "$0" >&2
  exit 2
fi

mvn -s "$MAVEN_SETTINGS_FILE" -Dmaven.repo.local="$MAVEN_REPO_LOCAL" -Dlicense.skip=true -f pom.xml -pl memind-evaluation -am -DskipTests compile
./dev-tools/full-qa-c019-regression-java.sh "$@"
