#!/bin/sh
# Builds the debug APK (app/build/outputs/apk/debug/app-debug.apk).
# Gradle/AGP want a JDK 17-21: use JAVA_HOME if it is one, else look for one.
set -e
cd "$(dirname "$0")"

jdk_major() { "$1/bin/java" -version 2>&1 | sed -n 's/.*version "\([0-9]*\).*/\1/p' | head -1; }
ok() { [ -x "$1/bin/java" ] && v=$(jdk_major "$1") && [ "$v" -ge 17 ] 2>/dev/null && [ "$v" -le 21 ]; }

if [ -z "$JAVA_HOME" ] || ! ok "$JAVA_HOME"; then
    JAVA_HOME=""
    for d in "$HOME"/.gradle/jdks/jdk-21* "$HOME"/.gradle/jdks/*21* "$HOME"/.gradle/jdks/jdk-17* \
             /usr/lib/jvm/java-21-* /usr/lib/jvm/java-17-* /usr/lib/jvm/*21* /usr/lib/jvm/*17* \
             /opt/android-studio/jbr "$HOME"/android-studio/jbr; do
        if ok "$d"; then JAVA_HOME="$d"; break; fi
    done
fi
if [ -z "$JAVA_HOME" ]; then
    echo "No JDK 17-21 found. Download one (e.g. Eclipse Temurin 21) into ~/.gradle/jdks/ or set JAVA_HOME." >&2
    exit 1
fi
export JAVA_HOME
echo "Using JDK: $JAVA_HOME"
[ $# -eq 0 ] && set -- assembleDebug
# no swap on the build machine: cap the build so only it dies if it grows too much (exit 137)
if command -v systemd-run >/dev/null 2>&1 && systemd-run --user --scope --quiet true 2>/dev/null; then
    exec systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 --quiet ./gradlew "$@"
fi
exec ./gradlew "$@"
